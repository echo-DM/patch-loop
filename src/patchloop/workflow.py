from __future__ import annotations

import argparse
import json
import os
import sys
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
    VerifierAdapter,
)
from patchloop.config import RepositoryConfig, load_repository_config
from patchloop.core import RunAdapters, RunRequest, RunResult, run
from patchloop.docker_verifier import DockerVerifierAdapter
from patchloop.errors import ConfigError, InfrastructureError, PatchLoopError, TaskError
from patchloop.gemini import GeminiPatchModelAdapter
from patchloop.github_api import GitHubApiClient
from patchloop.github_publish import run_publish
from patchloop.sanitize import redact_text
from patchloop.workflow_artifacts import (
    ArtifactIntegrityError,
    FrozenCommentDocument,
    FrozenTaskDocument,
    GateReportDocument,
    PublicationContextDocument,
    json_bytes,
    load_frozen_task,
    load_publication_context,
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
    publication_context: PublicationContextDocument | None = None
    try:
        resolution = GitHubIssueAdapter(client).resolve(event)
        if not isinstance(resolution, GateRejection):
            publication_context = _publication_context(event, resolution.id)
    except Exception:
        resolution = GateRejection(
            task_id="github-event",
            code="github_gate_failed",
            message="PatchLoop could not safely resolve the GitHub Issue event.",
        )
    payloads: dict[str, bytes]
    if isinstance(resolution, GateRejection):
        authorized = False
        gate_report = GateReportDocument(
            gate_version="1",
            authorized=False,
            task_id=resolution.task_id,
            code=resolution.code,
            message=resolution.message,
        )
        payloads = {"gate-report.json": json_bytes(gate_report)}
    else:
        authorized = True
        assert publication_context is not None
        gate_report = GateReportDocument(
            gate_version="1",
            authorized=True,
            task_id=resolution.id,
            code="authorized",
            message="The triggering actor may run PatchLoop.",
        )
        assert resolution.authorized_by is not None
        task = FrozenTaskDocument(
            task_version="1",
            id=redact_text(resolution.id),
            title=redact_text(resolution.title),
            body=redact_text(resolution.body),
            authorized_by=redact_text(resolution.authorized_by),
            supplemental_requirements=[
                _comment_document(comment)
                for comment in resolution.supplemental_requirements
            ],
            reference_material=[
                _comment_document(comment) for comment in resolution.reference_material
            ],
        )
        payloads = {
            "gate-report.json": json_bytes(gate_report),
            "publication-context.json": json_bytes(publication_context),
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


def _publication_context(
    event: Mapping[str, object], task_id: str
) -> PublicationContextDocument:
    repository = event.get("repository")
    issue = event.get("issue")
    if not isinstance(repository, dict) or not isinstance(issue, dict):
        raise ValueError("GitHub publication context is invalid.")
    repository_document = cast(dict[str, object], repository)
    issue_document = cast(dict[str, object], issue)
    full_name = repository_document.get("full_name")
    base_branch = repository_document.get("default_branch")
    issue_number = issue_document.get("number")
    if (
        not isinstance(full_name, str)
        or not isinstance(base_branch, str)
        or not base_branch
        or isinstance(issue_number, bool)
        or not isinstance(issue_number, int)
        or issue_number <= 0
    ):
        raise ValueError("GitHub publication context is invalid.")
    return PublicationContextDocument(
        context_version="1",
        task_id=task_id,
        repository=full_name,
        issue_number=issue_number,
        base_branch=base_branch,
    )


def _comment_document(comment: IssueComment) -> FrozenCommentDocument:
    return FrozenCommentDocument(
        id=comment.id,
        author=redact_text(comment.author),
        body=redact_text(comment.body),
    )


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
    task, config, publication_context = _load_agent_inputs(
        task_path, repository, config_path
    )
    selected_model = model or GeminiPatchModelAdapter.from_environment(config.model)
    return _run_loaded_agent(
        task=task,
        repository=repository,
        config=config,
        output=output,
        model=selected_model,
        verifier=verifier or DockerVerifierAdapter(),
        publication_context=publication_context,
    )


def _load_agent_inputs(
    task_path: Path, repository: Path, config_path: str
) -> tuple[
    EvaluationTask, RepositoryConfig, PublicationContextDocument | None
]:
    return (
        load_frozen_task(task_path),
        load_repository_config(_repository_file(repository, config_path)),
        load_publication_context(task_path),
    )


def _run_loaded_agent(
    *,
    task: EvaluationTask,
    repository: Path,
    config: RepositoryConfig,
    output: Path,
    model: PatchModel,
    verifier: VerifierAdapter,
    publication_context: PublicationContextDocument | None = None,
) -> RunResult:
    result = run(
        RunRequest(
            task=task,
            repository=repository,
            config=config,
            adapters=RunAdapters(
                model=model,
                verifier=verifier,
            ),
        )
    )
    payloads: dict[str, bytes] = {
        "run-report.json": json_bytes(result.report),
        "publication-intent.json": json_bytes(result.publication),
    }
    if result.patch is not None:
        payloads["patch.diff"] = result.patch.content.encode()
    if publication_context is not None:
        payloads["publication-context.json"] = json_bytes(publication_context)
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
    publish = commands.add_parser("publish")
    publish.add_argument("--artifact", type=Path, required=True)
    publish.add_argument("--repository", required=True)
    publish.add_argument("--issue", type=int, required=True)
    publish.add_argument("--base", required=True)
    publish.add_argument("--summary", type=Path)
    gate = commands.add_parser("gate")
    gate.add_argument("--output", type=Path, required=True)
    gate.add_argument("--github-output", type=Path, required=True)
    gate.add_argument("--summary", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "agent":
        task: EvaluationTask | None = None
        config: RepositoryConfig | None = None
        publication_context: PublicationContextDocument | None = None
        try:
            repository = cast(Path, arguments.repository)
            task_path = cast(Path, arguments.task)
            task = load_frozen_task(task_path)
            publication_context = load_publication_context(task_path)
            config = load_repository_config(
                _repository_file(repository, cast(str, arguments.config))
            )
            _run_loaded_agent(
                task=task,
                repository=repository,
                config=config,
                output=cast(Path, arguments.output),
                model=GeminiPatchModelAdapter.from_environment(config.model),
                verifier=DockerVerifierAdapter(),
                publication_context=publication_context,
            )
        except (ArtifactIntegrityError, PatchLoopError) as error:
            write_agent_failure_artifact(
                cast(Path, arguments.output),
                error,
                task_id=task.id if task is not None else None,
                config=config,
                publication_context=publication_context,
            )
        return 0
    try:
        if arguments.command == "verify":
            verify_artifact(
                cast(Path, arguments.artifact),
                summary=cast(Path | None, arguments.summary),
            )
        elif arguments.command == "publish":
            run_publish(
                artifact=cast(Path, arguments.artifact),
                repository=cast(str, arguments.repository),
                issue_number=cast(int, arguments.issue),
                base_branch=cast(str, arguments.base),
                client=GitHubApiClient(
                    api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                    token=os.environ.get("GITHUB_TOKEN", ""),
                ),
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
