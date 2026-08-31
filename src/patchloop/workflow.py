from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from patchloop.adapters import (
    EvaluationTask,
    GateRejection,
    GitHubClient,
    GitHubIssueAdapter,
    IssueComment,
    PatchModel,
    RepositoryPermission,
    VerifierAdapter,
)
from patchloop.config import load_repository_config
from patchloop.core import RunAdapters, RunRequest, RunResult, run
from patchloop.docker_verifier import DockerVerifierAdapter
from patchloop.errors import ConfigError, InfrastructureError, TaskError
from patchloop.gemini import GeminiPatchModelAdapter
from patchloop.sanitize import redact_text


ARTIFACT_VERSION = "1"


class ArtifactIntegrityError(ValueError):
    """The transferred workflow result does not match its integrity manifest."""


class GitHubApiClient:
    """Read only the permission and comment data required by the Gate."""

    def __init__(self, *, api_url: str, token: str) -> None:
        if not token.strip():
            raise InfrastructureError(
                "github_token_missing", "The Gate requires the caller GITHUB_TOKEN."
            )
        self._api_url = api_url.rstrip("/")
        self._token = token

    def permission_for(
        self, repository: str, username: str
    ) -> RepositoryPermission | None:
        path = (
            f"/repos/{urllib.parse.quote(repository, safe='/')}/collaborators/"
            f"{urllib.parse.quote(username, safe='')}/permission"
        )
        try:
            document = self._get(path)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise
        if not isinstance(document, dict):
            raise ValueError("GitHub permission response must be an object.")
        permission = cast(dict[str, object], document).get("permission")
        if permission not in {"admin", "write", "read", "none"}:
            raise ValueError("GitHub permission response is invalid.")
        return cast(RepositoryPermission, permission)

    def issue_comments(
        self, repository: str, issue_number: int
    ) -> list[dict[str, object]]:
        comments: list[dict[str, object]] = []
        page = 1
        while True:
            path = (
                f"/repos/{urllib.parse.quote(repository, safe='/')}/issues/"
                f"{issue_number}/comments?per_page=100&page={page}"
            )
            document = self._get(path)
            if not isinstance(document, list) or not all(
                isinstance(item, dict) for item in document
            ):
                raise ValueError("GitHub comments response must be a list of objects.")
            page_comments = cast(list[dict[str, object]], document)
            comments.extend(page_comments)
            if len(page_comments) < 100:
                return comments
            page += 1

    def _get(self, path: str) -> object:
        request = urllib.request.Request(
            self._api_url + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())


def run_gate(
    *,
    event: Mapping[str, object],
    client: GitHubClient,
    output: Path,
    github_output: Path,
    summary: Path | None = None,
) -> bool:
    """Authorize an Issue event and freeze the task before any Agent starts."""
    try:
        resolution = GitHubIssueAdapter(client).resolve(event)
    except Exception:
        resolution = GateRejection(
            task_id="github-event",
            code="github_gate_failed",
            message="PatchLoop could not safely resolve the GitHub Issue event.",
        )
    payloads: dict[str, bytes]
    if isinstance(resolution, GateRejection):
        authorized = False
        gate_report = {
            "gate_version": "1",
            "authorized": False,
            "task_id": resolution.task_id,
            "code": resolution.code,
            "message": resolution.message,
        }
        payloads = {"gate-report.json": _json_bytes(gate_report)}
    else:
        authorized = True
        gate_report = {
            "gate_version": "1",
            "authorized": True,
            "task_id": resolution.id,
            "code": "authorized",
            "message": "The triggering actor may run PatchLoop.",
        }
        task = {
            "task_version": "1",
            "id": redact_text(resolution.id),
            "title": redact_text(resolution.title),
            "body": redact_text(resolution.body),
            "authorized_by": (
                redact_text(resolution.authorized_by)
                if resolution.authorized_by is not None
                else None
            ),
            "supplemental_requirements": [
                _comment_document(comment)
                for comment in resolution.supplemental_requirements
            ],
            "reference_material": [
                _comment_document(comment) for comment in resolution.reference_material
            ],
        }
        payloads = {
            "gate-report.json": _json_bytes(gate_report),
            "task.json": _json_bytes(task),
        }
    _write_artifact(output, payloads)
    with github_output.open("a") as stream:
        stream.write(f"authorized={str(authorized).lower()}\n")
    if summary is not None:
        with summary.open("a") as stream:
            stream.write(
                "## PatchLoop Gate\n\n"
                f"- Status: {'authorized' if authorized else 'failed'}\n"
                f"- Code: {gate_report['code']}\n"
            )
    return authorized


def _comment_document(comment: IssueComment) -> dict[str, object]:
    return {
        "id": comment.id,
        "author": redact_text(comment.author),
        "body": redact_text(comment.body),
    }


def run_agent(
    *,
    task_path: Path,
    repository: Path,
    config_path: str,
    output: Path,
    model: PatchModel | None = None,
    verifier: VerifierAdapter | None = None,
) -> RunResult:
    """Run the public core seam and emit the Agent-to-Publish artifact."""
    task = _load_frozen_task(task_path)
    config_file = _repository_file(repository, config_path)
    config = load_repository_config(config_file)
    selected_model = model or GeminiPatchModelAdapter.from_environment(config.model)
    result = run(
        RunRequest(
            task=task,
            repository=repository,
            config=config,
            adapters=RunAdapters(
                model=selected_model,
                verifier=verifier or DockerVerifierAdapter(),
            ),
        )
    )
    payloads: dict[str, bytes] = {
        "run-report.json": _json_bytes(result.report),
        "publication-intent.json": _json_bytes(result.publication),
    }
    if result.patch is not None:
        payloads["patch.diff"] = result.patch.content.encode()
    _write_artifact(output, payloads)
    return result


def verify_artifact(
    artifact: Path, *, summary: Path | None = None
) -> dict[str, object]:
    """Verify every declared payload before a privileged stage consumes it."""
    payloads = _verified_payload_bytes(
        artifact, {"patch.diff", "publication-intent.json", "run-report.json"}
    )
    verified: dict[str, object] = {}
    for name, content in payloads.items():
        if name.endswith(".json"):
            verified[name] = _json_bytes_mapping(content, name)
        else:
            try:
                verified[name] = content.decode()
            except UnicodeDecodeError as error:
                raise ArtifactIntegrityError("Patch payload is not UTF-8 text.") from error
    _verify_result_consistency(verified)
    if summary is not None:
        _append_summary(summary, cast(dict[str, object], verified["run-report.json"]))
    return verified


def _verified_payload_bytes(
    artifact: Path, allowed_names: set[str]
) -> dict[str, bytes]:
    manifest = _json_mapping(artifact / "manifest.json")
    if manifest.get("artifact_version") != ARTIFACT_VERSION:
        raise ArtifactIntegrityError("Unsupported PatchLoop artifact version.")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise ArtifactIntegrityError("Artifact manifest has no declared payloads.")
    files = cast(dict[str, object], raw_files)
    if not set(files).issubset(allowed_names):
        raise ArtifactIntegrityError("Artifact declares an unsupported payload.")
    expected_names = set(files) | {"manifest.json"}
    paths = list(artifact.iterdir())
    actual_names = {
        path.name for path in paths if path.is_file() and not path.is_symlink()
    }
    if actual_names != expected_names or any(path.is_symlink() for path in paths):
        raise ArtifactIntegrityError("Artifact contains missing or undeclared files.")
    verified: dict[str, bytes] = {}
    for name, raw_metadata in files.items():
        if not isinstance(raw_metadata, dict):
            raise ArtifactIntegrityError("Artifact file metadata is invalid.")
        content = artifact.joinpath(name).read_bytes()
        if cast(dict[str, object], raw_metadata) != {
            "byte_length": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }:
            raise ArtifactIntegrityError(
                f"Artifact payload failed integrity check: {name}."
            )
        verified[name] = content
    return verified


def _write_artifact(output: Path, payloads: Mapping[str, bytes]) -> None:
    output.mkdir(parents=True, exist_ok=False)
    files: dict[str, dict[str, object]] = {}
    for name, content in payloads.items():
        output.joinpath(name).write_bytes(content)
        files[name] = {
            "byte_length": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    output.joinpath("manifest.json").write_bytes(
        _json_bytes({"artifact_version": ARTIFACT_VERSION, "files": files})
    )


def _verify_result_consistency(verified: Mapping[str, object]) -> None:
    report = _object_mapping(verified.get("run-report.json"), "run report")
    publication = _object_mapping(
        verified.get("publication-intent.json"), "publication intent"
    )
    patch = verified.get("patch.diff")
    report_patch = report.get("patch")
    if patch is None:
        if report_patch is not None or publication.get("intent") == "draft_pr":
            raise ArtifactIntegrityError("Patch metadata exists without a patch payload.")
        return
    if not isinstance(patch, str) or not isinstance(report_patch, dict):
        raise ArtifactIntegrityError("Patch payload metadata is invalid.")
    patch_hash = hashlib.sha256(patch.encode()).hexdigest()
    integrity = _object_mapping(report.get("integrity"), "run report integrity")
    if (
        cast(dict[str, object], report_patch).get("sha256") != patch_hash
        or integrity.get("algorithm") != "sha256"
        or integrity.get("patch_sha256") != patch_hash
        or publication.get("intent") != "draft_pr"
        or publication.get("patch_sha256") != patch_hash
    ):
        raise ArtifactIntegrityError("Patch identity is inconsistent across the artifact.")


def _append_summary(path: Path, report: Mapping[str, object]) -> None:
    verification = _object_mapping(report.get("verification"), "verification report")
    budgets = _object_mapping(report.get("budgets"), "budget report")
    usage = _object_mapping(budgets.get("usage"), "budget usage")
    limits = _object_mapping(budgets.get("limits"), "budget limits")
    rows = (
        ("Terminal outcome", report.get("terminal_outcome")),
        ("Checks", verification.get("status")),
        ("Iterations", _ratio(usage, limits, "iterations", "max_iterations")),
        ("Tool calls", _ratio(usage, limits, "tool_calls", "max_tool_calls")),
        ("Changed files", _ratio(usage, limits, "changed_files", "max_changed_files")),
        ("Diff lines", _ratio(usage, limits, "diff_lines", "max_diff_lines")),
        (
            "Wall time (minutes)",
            _ratio(usage, limits, "wall_time_minutes", "max_wall_time_minutes"),
        ),
    )
    with path.open("a") as stream:
        stream.write("## PatchLoop\n\n| Field | Result |\n| --- | --- |\n")
        for label, value in rows:
            stream.write(f"| {label} | {_summary_scalar(value)} |\n")


def _ratio(
    usage: Mapping[str, object], limits: Mapping[str, object], used: str, maximum: str
) -> str:
    return f"{_summary_scalar(usage.get(used))} / {_summary_scalar(limits.get(maximum))}"


def _summary_scalar(value: object) -> str:
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        return str(value).replace("|", "\\|").replace("\n", " ")[:100]
    raise ArtifactIntegrityError("Run report contains an invalid summary value.")


def _repository_file(repository: Path, relative_path: str) -> Path:
    root = repository.resolve()
    candidate_path = Path(relative_path)
    if candidate_path.is_absolute():
        raise ConfigError(
            "invalid_config_path",
            "Workflow configuration path must be repository-relative.",
        )
    candidate = (root / candidate_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ConfigError(
            "invalid_config_path",
            "Workflow configuration path must stay in the repository.",
        ) from error
    return candidate


def _load_frozen_task(path: Path) -> EvaluationTask:
    if path.parent.joinpath("manifest.json").exists():
        payloads = _verified_payload_bytes(path.parent, {"gate-report.json", "task.json"})
        try:
            content = payloads[path.name]
        except KeyError as error:
            raise ArtifactIntegrityError("Gate artifact has no frozen task.") from error
        document = _json_bytes_mapping(content, path.name)
    else:
        document = _json_mapping(path)
    if document.get("task_version") != "1":
        raise TaskError("invalid_task", "Unsupported frozen task version.")
    task_id = _required_string(document, "id")
    title = _required_string(document, "title")
    body = _required_string(document, "body", allow_empty=True)
    authorized_by = _required_string(document, "authorized_by")
    return EvaluationTask(
        id=task_id,
        title=title,
        body=body,
        authorized_by=authorized_by,
        supplemental_requirements=_load_comments(document, "supplemental_requirements"),
        reference_material=_load_comments(document, "reference_material"),
    )


def _load_comments(document: Mapping[str, object], field: str) -> tuple[IssueComment, ...]:
    raw_comments = document.get(field)
    if not isinstance(raw_comments, list):
        raise TaskError("invalid_task", f"Frozen task field {field} must be a list.")
    comments: list[IssueComment] = []
    for raw_comment in raw_comments:
        if not isinstance(raw_comment, dict):
            raise TaskError("invalid_task", f"Frozen task field {field} is invalid.")
        comment = cast(dict[str, object], raw_comment)
        comment_id = comment.get("id")
        if isinstance(comment_id, bool) or not isinstance(comment_id, int):
            raise TaskError("invalid_task", f"Frozen task field {field} is invalid.")
        comments.append(
            IssueComment(
                id=comment_id,
                author=_required_string(comment, "author"),
                body=_required_string(comment, "body", allow_empty=True),
            )
        )
    return tuple(comments)


def _required_string(
    document: Mapping[str, object], field: str, *, allow_empty: bool = False
) -> str:
    value = document.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise TaskError("invalid_task", f"Frozen task field {field} must be a string.")
    return value


def _json_mapping(path: Path) -> dict[str, object]:
    try:
        return _json_bytes_mapping(path.read_bytes(), path.name)
    except OSError as error:
        raise ArtifactIntegrityError(f"Invalid JSON payload: {path.name}.") from error


def _json_bytes_mapping(content: bytes, name: str) -> dict[str, object]:
    try:
        loaded = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise ArtifactIntegrityError(f"Invalid JSON payload: {name}.") from error
    if not isinstance(loaded, dict):
        raise ArtifactIntegrityError(f"JSON payload must be an object: {name}.")
    return cast(dict[str, object], loaded)


def _object_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"Artifact {label} must be an object.")
    return cast(dict[str, object], value)


def _json_bytes(document: object) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m patchloop.workflow")
    commands = parser.add_subparsers(dest="command", required=True)
    agent = commands.add_parser("agent")
    agent.add_argument("--task", type=Path, required=True)
    agent.add_argument("--repository", type=Path, required=True)
    agent.add_argument("--config", required=True)
    agent.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--artifact", type=Path, required=True)
    verify.add_argument("--summary", type=Path)
    gate = commands.add_parser("gate")
    gate.add_argument("--output", type=Path, required=True)
    gate.add_argument("--github-output", type=Path, required=True)
    gate.add_argument("--summary", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "agent":
            run_agent(
                task_path=cast(Path, arguments.task),
                repository=cast(Path, arguments.repository),
                config_path=cast(str, arguments.config),
                output=cast(Path, arguments.output),
            )
        elif arguments.command == "verify":
            verify_artifact(
                cast(Path, arguments.artifact),
                summary=cast(Path | None, arguments.summary),
            )
        else:
            raw_event = os.environ.get("PATCHLOOP_EVENT_JSON", "")
            event = json.loads(raw_event)
            if not isinstance(event, dict):
                raise TaskError("invalid_event", "The GitHub event must be a JSON object.")
            run_gate(
                event=cast(dict[str, object], event),
                client=GitHubApiClient(
                    api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                    token=os.environ.get("GITHUB_TOKEN", ""),
                ),
                output=cast(Path, arguments.output),
                github_output=cast(Path, arguments.github_output),
                summary=cast(Path | None, arguments.summary),
            )
    except (
        ArtifactIntegrityError,
        ConfigError,
        InfrastructureError,
        TaskError,
        json.JSONDecodeError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
