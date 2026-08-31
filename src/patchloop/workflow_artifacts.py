from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TypedDict, cast

from patchloop.adapters import EvaluationTask, IssueComment
from patchloop.config import RepositoryConfig, SAFETY_CEILINGS
from patchloop.controlled_tools import MAX_PATCHED_FILE_BYTES
from patchloop.errors import ConfigError, InfrastructureError, PatchLoopError, TaskError
from patchloop.sanitize import redact_text


ARTIFACT_VERSION = "1"
RUN_PAYLOADS = {"patch.diff", "publication-intent.json", "run-report.json"}
GATE_PAYLOADS = {"gate-report.json", "task.json"}


class ArtifactFileMetadata(TypedDict):
    byte_length: int
    sha256: str


class ArtifactManifest(TypedDict):
    artifact_version: Literal["1"]
    files: dict[str, ArtifactFileMetadata]


class FrozenCommentDocument(TypedDict):
    id: int
    author: str
    body: str


class FrozenTaskDocument(TypedDict):
    task_version: Literal["1"]
    id: str
    title: str
    body: str
    authorized_by: str
    supplemental_requirements: list[FrozenCommentDocument]
    reference_material: list[FrozenCommentDocument]


class GateReportDocument(TypedDict):
    gate_version: Literal["1"]
    authorized: bool
    task_id: str
    code: str
    message: str


class ArtifactIntegrityError(ValueError):
    """The transferred workflow result does not match its integrity manifest."""


def json_bytes(document: object) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_artifact(output: Path, payloads: Mapping[str, bytes]) -> None:
    output.mkdir(parents=True, exist_ok=False)
    files: dict[str, ArtifactFileMetadata] = {}
    for name, content in payloads.items():
        output.joinpath(name).write_bytes(content)
        files[name] = {
            "byte_length": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    manifest = ArtifactManifest(artifact_version="1", files=files)
    output.joinpath("manifest.json").write_bytes(json_bytes(manifest))


def write_agent_failure_artifact(
    output: Path,
    error: PatchLoopError | ArtifactIntegrityError,
    *,
    task_id: str | None = None,
    config: RepositoryConfig | None = None,
) -> None:
    """Preserve a sanitized terminal report when Agent setup cannot start core.run."""
    if isinstance(error, ConfigError):
        category = "configuration"
        code = error.code
        message = error.message
    elif isinstance(error, TaskError):
        category = "task"
        code = error.code
        message = error.message
    elif isinstance(error, InfrastructureError):
        category = "infrastructure"
        code = error.code
        message = error.message
    else:
        category = "integrity"
        code = "invalid_gate_artifact"
        message = "The frozen Gate artifact failed validation."
    error_document = {
        "category": category,
        "code": code,
        "message": redact_text(message),
    }
    publication = {"intent": "none", "reason": "failed"}
    if config is None:
        model_name = "unavailable"
        configured_checks: list[str] = []
        budget_limits = {
            **SAFETY_CEILINGS,
            "max_file_bytes": MAX_PATCHED_FILE_BYTES,
        }
    else:
        model_name = redact_text(config.model)
        configured_checks = [redact_text(command) for command in config.verifier.checks]
        budget_limits = {
            "max_iterations": config.budgets.max_iterations,
            "max_tool_calls": config.budgets.max_tool_calls,
            "max_changed_files": config.budgets.max_changed_files,
            "max_diff_lines": config.budgets.max_diff_lines,
            "max_wall_time_minutes": config.budgets.max_wall_time_minutes,
            "max_file_bytes": MAX_PATCHED_FILE_BYTES,
        }
    report = {
        "report_version": "1",
        "task_id": redact_text(task_id) if task_id is not None else "unavailable",
        "model": {"provider": "configured", "name": model_name},
        "terminal_outcome": "failed",
        "summary": "PatchLoop could not start the bounded Agent run.",
        "actionable_message": redact_text(message),
        "patch": None,
        "changed_files": {"count": 0, "paths": []},
        "verification": {
            "status": "not_run",
            "configured_checks": configured_checks,
            "setup": [],
            "checks": [],
            "attempts": [],
        },
        "budgets": {
            "limits": budget_limits,
            "usage": {
                "iterations": 0,
                "tool_calls": 0,
                "changed_files": 0,
                "diff_lines": 0,
                "wall_time_minutes": 0,
            },
            "resource_limit_events": [],
        },
        "publication": publication,
        "errors": [error_document],
    }
    write_artifact(
        output,
        {
            "run-report.json": json_bytes(report),
            "publication-intent.json": json_bytes(publication),
        },
    )


def verify_artifact(
    artifact: Path, *, summary: Path | None = None
) -> dict[str, object]:
    """Verify every declared payload before a privileged stage consumes it."""
    payloads = _verified_payload_bytes(artifact, RUN_PAYLOADS)
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


def load_frozen_task(path: Path) -> EvaluationTask:
    if path.parent.joinpath("manifest.json").exists():
        payloads = _verified_payload_bytes(path.parent, GATE_PAYLOADS)
        try:
            content = payloads[path.name]
        except KeyError as error:
            raise ArtifactIntegrityError("Gate artifact has no frozen task.") from error
        document = _json_bytes_mapping(content, path.name)
    else:
        document = _json_mapping(path)
    if document.get("task_version") != "1":
        raise TaskError("invalid_task", "Unsupported frozen task version.")
    return EvaluationTask(
        id=_required_string(document, "id"),
        title=_required_string(document, "title"),
        body=_required_string(document, "body", allow_empty=True),
        authorized_by=_required_string(document, "authorized_by"),
        supplemental_requirements=_load_comments(document, "supplemental_requirements"),
        reference_material=_load_comments(document, "reference_material"),
    )


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


def _verify_result_consistency(verified: Mapping[str, object]) -> None:
    report = _object_mapping(verified.get("run-report.json"), "run report")
    publication = _object_mapping(
        verified.get("publication-intent.json"), "publication intent"
    )
    if report.get("publication") != publication:
        raise ArtifactIntegrityError(
            "Publication intent is inconsistent across the artifact."
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
    except json.JSONDecodeError as error:
        raise ArtifactIntegrityError(f"Invalid JSON payload: {name}.") from error
    if not isinstance(loaded, dict):
        raise ArtifactIntegrityError(f"JSON payload must be an object: {name}.")
    return cast(dict[str, object], loaded)


def _object_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"Artifact {label} must be an object.")
    return cast(dict[str, object], value)
