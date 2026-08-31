from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from patchloop.errors import InfrastructureError
from patchloop.publication_patch import FilePatch, apply_file_patch, validate_patch
from patchloop.publication_report import pull_request_body
from patchloop.workflow_artifacts import (
    ArtifactIntegrityError,
    append_run_summary,
    verify_artifact,
)


_TASK_ID = re.compile(r"github:([^/#]+/[^/#]+)#([1-9][0-9]*)\Z")
_UNSAFE_REF_CHARS = frozenset(" ~^:?*[\\")


@dataclass(frozen=True)
class BaseRevision:
    commit_sha: str
    tree_sha: str


@dataclass(frozen=True)
class BaseFile:
    path: str
    content: bytes | None
    mode: Literal["100644", "100755"]


@dataclass(frozen=True)
class CommitFile:
    path: str
    content: bytes
    mode: Literal["100644", "100755"]


@dataclass(frozen=True)
class DraftPullRequest:
    number: int
    url: str


class GitHubPublisher(Protocol):
    def base_revision(self, repository: str, branch: str) -> BaseRevision: ...

    def base_files(
        self,
        repository: str,
        base: BaseRevision,
        paths: tuple[str, ...],
    ) -> tuple[BaseFile, ...]: ...

    def branch_head(self, repository: str, branch: str) -> str | None: ...

    def create_commit(
        self,
        repository: str,
        base: BaseRevision,
        files: tuple[CommitFile, ...],
        message: str,
    ) -> str: ...

    def create_branch(self, repository: str, branch: str, commit_sha: str) -> None: ...

    def create_draft_pull_request(
        self,
        repository: str,
        *,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
    ) -> DraftPullRequest: ...

    def delete_branch(self, repository: str, branch: str) -> None: ...


def run_publish(
    *,
    artifact: Path,
    repository: str,
    issue_number: int,
    base_branch: str,
    client: GitHubPublisher,
    summary: Path | None = None,
) -> DraftPullRequest | None:
    """Validate one Agent artifact, then create its controlled Draft PR."""
    verified = verify_artifact(artifact)
    report = _mapping(verified.get("run-report.json"), "run report")
    publication = _mapping(
        verified.get("publication-intent.json"), "publication intent"
    )
    if publication.get("intent") != "draft_pr":
        return None
    context = _mapping(
        verified.get("publication-context.json"), "publication context"
    )
    _validate_identity(report, context, repository, issue_number, base_branch)
    patch = verified.get("patch.diff")
    if not isinstance(patch, str) or not patch:
        raise ArtifactIntegrityError("Draft PR publication requires a non-empty patch.")
    file_patches = validate_patch(report, patch)

    branch = f"patchloop/issue-{issue_number}"
    try:
        base = client.base_revision(repository, base_branch)
        base_files = client.base_files(
            repository, base, tuple(item.path for item in file_patches)
        )
        existing_branch = client.branch_head(repository, branch)
    except ArtifactIntegrityError:
        raise
    except Exception as error:
        raise InfrastructureError(
            "github_publish_read_failed",
            "Publish could not validate the patch against the requested base branch.",
        ) from error
    if existing_branch is not None:
        raise InfrastructureError(
            "github_branch_exists",
            "The controlled Issue branch already exists; idempotent updates require Ticket 12.",
        )
    files = _commit_files(file_patches, base_files)
    body = pull_request_body(report, issue_number)
    if summary is not None:
        append_run_summary(summary, report)
    try:
        commit_sha = client.create_commit(
            repository,
            base,
            files,
            f"patchloop: address issue #{issue_number}",
        )
    except Exception as error:
        raise InfrastructureError(
            "github_commit_failed",
            "Publish could not create the ordinary commit for the Issue branch.",
        ) from error
    try:
        client.create_branch(repository, branch, commit_sha)
    except Exception as error:
        _handle_uncertain_branch_creation(
            client, repository, branch, commit_sha, error
        )
    try:
        return client.create_draft_pull_request(
            repository,
            base_branch=base_branch,
            head_branch=branch,
            title=f"PatchLoop: address #{issue_number}",
            body=body,
        )
    except Exception as error:
        try:
            client.delete_branch(repository, branch)
        except Exception as rollback_error:
            raise InfrastructureError(
                "github_publish_rollback_failed",
                "Draft PR creation failed and the controlled branch could not be removed.",
            ) from rollback_error
        raise InfrastructureError(
            "github_pull_request_failed",
            "Draft PR creation failed; the controlled branch was removed.",
        ) from error


def _commit_files(
    patches: tuple[FilePatch, ...], base_files: tuple[BaseFile, ...]
) -> tuple[CommitFile, ...]:
    if tuple(item.path for item in base_files) != tuple(item.path for item in patches):
        raise ArtifactIntegrityError(
            "GitHub base files do not match the declared patch paths."
        )
    files: list[CommitFile] = []
    for patch, base_file in zip(patches, base_files, strict=True):
        if base_file.mode not in {"100644", "100755"}:
            raise ArtifactIntegrityError("GitHub base file mode is not publishable.")
        files.append(
            CommitFile(
                patch.path,
                apply_file_patch(patch, base_file.content),
                base_file.mode,
            )
        )
    return tuple(files)


def _handle_uncertain_branch_creation(
    client: GitHubPublisher,
    repository: str,
    branch: str,
    commit_sha: str,
    original_error: Exception,
) -> None:
    try:
        observed_sha = client.branch_head(repository, branch)
    except Exception as lookup_error:
        raise InfrastructureError(
            "github_branch_creation_uncertain",
            "Publish could not determine whether GitHub created the controlled branch.",
        ) from lookup_error
    if observed_sha == commit_sha:
        try:
            client.delete_branch(repository, branch)
        except Exception as rollback_error:
            raise InfrastructureError(
                "github_publish_rollback_failed",
                "Branch creation failed ambiguously and its rollback also failed.",
            ) from rollback_error
    elif observed_sha is not None:
        raise InfrastructureError(
            "github_branch_creation_uncertain",
            "A different controlled branch appeared while Publish was creating it.",
        ) from original_error
    raise InfrastructureError(
        "github_branch_creation_failed",
        "Publish could not create the controlled Issue branch.",
    ) from original_error


def _validate_identity(
    report: Mapping[str, object],
    context: Mapping[str, object],
    repository: str,
    issue_number: int,
    base_branch: str,
) -> None:
    task_id = report.get("task_id")
    match = _TASK_ID.fullmatch(task_id) if isinstance(task_id, str) else None
    if match is None or match.group(1) != repository or int(match.group(2)) != issue_number:
        raise ArtifactIntegrityError(
            "Artifact task identity does not match the Publish request."
        )
    if context != {
        "base_branch": base_branch,
        "context_version": "1",
        "issue_number": issue_number,
        "repository": repository,
        "task_id": task_id,
    }:
        raise ArtifactIntegrityError(
            "Artifact publication context does not match the Publish request."
        )
    if report.get("terminal_outcome") != "pr_created":
        raise ArtifactIntegrityError("Draft PR intent has an invalid terminal outcome.")
    verification = _mapping(report.get("verification"), "verification report")
    if verification.get("status") not in {
        "checks_passed",
        "checks_failed",
        "budget_exhausted",
    }:
        raise ArtifactIntegrityError(
            "Draft PR intent has an invalid verification status."
        )
    _validate_branch(base_branch)


def _validate_branch(branch: str) -> None:
    if (
        not branch
        or len(branch) > 255
        or branch.startswith("/")
        or branch.endswith(("/", ".", ".lock"))
        or ".." in branch
        or "//" in branch
        or "@{" in branch
        or any(character in _UNSAFE_REF_CHARS or ord(character) < 32 for character in branch)
    ):
        raise ArtifactIntegrityError("Publish base branch is invalid.")


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"Artifact {label} must be an object.")
    return cast(dict[str, object], value)
