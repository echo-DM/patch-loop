from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from patchloop.adapters import (
    GateRejection,
    GitHubClient,
    GitHubIssueAdapter,
    IssueComment,
    PatchModel,
    VerifierAdapter,
)
from patchloop.config import load_repository_config
from patchloop.core import RunAdapters, RunRequest, RunResult, run
from patchloop.docker_verifier import DockerVerifierAdapter
from patchloop.errors import ConfigError, InfrastructureError, PatchLoopError, TaskError
from patchloop.gemini import GeminiPatchModelAdapter
from patchloop.github_api import GitHubApiClient
from patchloop.sanitize import redact_text
from patchloop.workflow_artifacts import (
    ArtifactIntegrityError,
    json_bytes,
    load_frozen_task,
    verify_artifact,
    write_agent_failure_artifact,
    write_artifact,
)


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
        payloads = {"gate-report.json": json_bytes(gate_report)}
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
            "gate-report.json": json_bytes(gate_report),
            "task.json": json_bytes(task),
        }
    write_artifact(output, payloads)
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
    task = load_frozen_task(task_path)
    config = load_repository_config(_repository_file(repository, config_path))
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
        "run-report.json": json_bytes(result.report),
        "publication-intent.json": json_bytes(result.publication),
    }
    if result.patch is not None:
        payloads["patch.diff"] = result.patch.content.encode()
    write_artifact(output, payloads)
    return result


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
    if arguments.command == "agent":
        try:
            run_agent(
                task_path=cast(Path, arguments.task),
                repository=cast(Path, arguments.repository),
                config_path=cast(str, arguments.config),
                output=cast(Path, arguments.output),
            )
        except (ArtifactIntegrityError, PatchLoopError) as error:
            write_agent_failure_artifact(cast(Path, arguments.output), error)
        return 0
    try:
        if arguments.command == "verify":
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
