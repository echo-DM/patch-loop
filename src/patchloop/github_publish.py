from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, cast

from patchloop.errors import InfrastructureError
from patchloop.publication_feedback import no_patch_feedback
from patchloop.publication_patch import FilePatch, apply_file_patch, validate_patch
from patchloop.publication_report import pull_request_body
from patchloop.workflow_artifacts import (
    ArtifactIntegrityError,
    append_run_summary,
    verify_artifact,
)


_TASK_ID = re.compile(r"github:([^/#]+/[^/#]+)#([1-9][0-9]*)\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
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


@dataclass(frozen=True)
class PullRequestState:
    number: int
    url: str
    state: Literal["open", "closed"]
    merged: bool
    draft: bool
    base_branch: str
    head_branch: str
    head_sha: str
    mergeable: bool | None


@dataclass(frozen=True)
class PublicationPlan:
    action: Literal["create", "update", "complete", "blocked"]
    branch: str
    pull_request: PullRequestState | None
    code: str
    message: str


class GitHubPublisher(Protocol):
    def create_issue_comment(
        self, repository: str, issue_number: int, body: str
    ) -> None: ...

    def pull_request_for_branch(
        self, repository: str, branch: str
    ) -> PullRequestState | None: ...

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

    def update_branch(
        self, repository: str, branch: str, commit_sha: str
    ) -> None: ...

    def create_draft_pull_request(
        self,
        repository: str,
        *,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
    ) -> DraftPullRequest: ...

    def update_pull_request(
        self, repository: str, number: int, body: str
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
    context = _mapping(
        verified.get("publication-context.json"), "publication context"
    )
    _validate_context_identity(report, context, repository, issue_number, base_branch)
    if publication.get("intent") != "draft_pr":
        body = no_patch_feedback(report, publication)
        if summary is not None:
            append_run_summary(summary, report)
        try:
            client.create_issue_comment(repository, issue_number, body)
        except Exception as error:
            if summary is not None:
                with summary.open("a") as stream:
                    stream.write(
                        "\n- Issue feedback: failed "
                        "(`github_issue_feedback_failed`; no automatic retry)\n"
                    )
            raise InfrastructureError(
                "github_issue_feedback_failed",
                "Publish could not add the bounded outcome feedback to the Issue.",
            ) from error
        return None
    _validate_draft_pr_identity(report)
    patch = verified.get("patch.diff")
    if not isinstance(patch, str) or not patch:
        raise ArtifactIntegrityError("Draft PR publication requires a non-empty patch.")
    file_patches = validate_patch(report, patch)

    plan = resolve_publication(
        repository=repository,
        issue_number=issue_number,
        base_branch=base_branch,
        client=client,
    )
    if plan.action == "complete":
        if summary is not None:
            append_run_summary(summary, report)
            _append_publication_status(summary, plan)
        return None
    if plan.action == "blocked":
        if summary is not None:
            append_run_summary(summary, report)
            _append_publication_status(summary, plan)
        report_publication_block(client, repository, issue_number, plan)
        return None

    branch = plan.branch
    updating = plan.action == "update"
    pull_request = plan.pull_request
    try:
        source_branch = branch if updating else base_branch
        base = client.base_revision(repository, source_branch)
        base_files = client.base_files(
            repository, base, tuple(item.path for item in file_patches)
        )
    except ArtifactIntegrityError:
        raise
    except InfrastructureError:
        raise
    except Exception as error:
        raise InfrastructureError(
            "github_publish_read_failed",
            "Publish could not validate the patch against the requested base branch.",
        ) from error
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
    if updating:
        assert pull_request is not None
        try:
            client.update_branch(repository, branch, commit_sha)
        except Exception as error:
            _handle_uncertain_branch_update(
                client,
                repository,
                branch,
                pull_request.head_sha,
                commit_sha,
                error,
            )
        try:
            return client.update_pull_request(repository, pull_request.number, body)
        except Exception as error:
            raise InfrastructureError(
                "github_pull_request_update_failed",
                "The commit was appended, but Publish could not update the Draft PR body.",
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
        return _handle_uncertain_pull_request_creation(
            client,
            repository,
            base_branch,
            branch,
            commit_sha,
            error,
        )


def resolve_publication(
    *,
    repository: str,
    issue_number: int,
    base_branch: str,
    client: GitHubPublisher,
) -> PublicationPlan:
    """Resolve durable branch and PR state before an Agent or Publish write."""
    if _REPOSITORY.fullmatch(repository) is None:
        raise ArtifactIntegrityError("Publish repository identity is invalid.")
    if isinstance(issue_number, bool) or issue_number <= 0:
        raise ArtifactIntegrityError("Publish Issue number is invalid.")
    _validate_branch(base_branch)
    branch = f"patchloop/issue-{issue_number}"
    try:
        pull_request = client.pull_request_for_branch(repository, branch)
        if pull_request is not None and pull_request.merged:
            return PublicationPlan(
                "complete",
                branch,
                pull_request,
                "github_pull_request_merged",
                "The existing PatchLoop pull request was merged; automated work is complete.",
            )
        if pull_request is not None and pull_request.state == "closed":
            return PublicationPlan(
                "blocked",
                branch,
                pull_request,
                "github_pull_request_closed",
                "The existing PatchLoop pull request was closed without merging. "
                "PatchLoop will not recreate it; an explicit restart mechanism is required.",
            )
        branch_head = client.branch_head(repository, branch)
    except Exception as error:
        raise InfrastructureError(
            "github_publish_state_read_failed",
            "PatchLoop could not determine the durable branch and pull request state.",
        ) from error
    if pull_request is None:
        if branch_head is not None:
            return PublicationPlan(
                "blocked",
                branch,
                None,
                "github_branch_without_pull_request",
                "The controlled Issue branch exists without a corresponding PatchLoop "
                "pull request. A maintainer must resolve this incompatible state.",
            )
        return PublicationPlan(
            "create",
            branch,
            None,
            "github_pull_request_missing",
            "PatchLoop will create the controlled Issue branch and Draft PR.",
        )
    if (
        not pull_request.draft
        or pull_request.base_branch != base_branch
        or pull_request.head_branch != branch
        or pull_request.head_sha != branch_head
    ):
        return PublicationPlan(
            "blocked",
            branch,
            pull_request,
            "github_pull_request_incompatible",
            "The existing PatchLoop pull request is incompatible with the requested "
            "base or controlled branch. No history was changed.",
        )
    if pull_request.mergeable is False:
        return PublicationPlan(
            "blocked",
            branch,
            pull_request,
            "github_merge_conflict",
            "The existing PatchLoop pull request has a merge conflict. PatchLoop will "
            "not rebase, rewrite history, or resolve it automatically.",
        )
    if pull_request.mergeable is None:
        return PublicationPlan(
            "blocked",
            branch,
            pull_request,
            "github_mergeability_unknown",
            "GitHub did not provide a definitive mergeability result. PatchLoop stopped "
            "without changing the existing pull request.",
        )
    return PublicationPlan(
        "update",
        branch,
        pull_request,
        "github_pull_request_active",
        "PatchLoop will append an ordinary commit to the active Draft PR.",
    )


def report_publication_block(
    client: GitHubPublisher,
    repository: str,
    issue_number: int,
    plan: PublicationPlan,
) -> None:
    body = f"## PatchLoop publication stopped\n\n{plan.message}\n\nCode: `{plan.code}`\n"
    try:
        client.create_issue_comment(repository, issue_number, body)
    except Exception as error:
        raise InfrastructureError(
            "github_issue_feedback_failed",
            "Publish stopped safely but could not report the lifecycle state on the Issue.",
        ) from error


def _append_publication_status(summary: Path, plan: PublicationPlan) -> None:
    with summary.open("a") as stream:
        stream.write(
            "\n## PatchLoop Publication State\n\n"
            f"- Code: `{plan.code}`\n"
            f"- Result: {plan.message}\n"
        )


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


def _handle_uncertain_branch_update(
    client: GitHubPublisher,
    repository: str,
    branch: str,
    previous_sha: str,
    commit_sha: str,
    original_error: Exception,
) -> None:
    try:
        observed_sha = client.branch_head(repository, branch)
    except Exception as lookup_error:
        raise InfrastructureError(
            "github_branch_update_uncertain",
            "Publish could not determine whether GitHub appended the ordinary commit.",
        ) from lookup_error
    if observed_sha == commit_sha:
        return
    if observed_sha == previous_sha:
        raise InfrastructureError(
            "github_branch_update_failed",
            "Publish could not append the ordinary commit to the Issue branch.",
        ) from original_error
    raise InfrastructureError(
        "github_branch_update_uncertain",
        "The Issue branch changed unexpectedly while Publish was updating it.",
    ) from original_error


def _handle_uncertain_pull_request_creation(
    client: GitHubPublisher,
    repository: str,
    base_branch: str,
    branch: str,
    commit_sha: str,
    original_error: Exception,
) -> DraftPullRequest:
    try:
        pull_request = client.pull_request_for_branch(repository, branch)
        observed_sha = client.branch_head(repository, branch)
    except Exception as lookup_error:
        raise InfrastructureError(
            "github_pull_request_creation_uncertain",
            "Publish could not determine whether GitHub created the Draft PR; the "
            "controlled branch was preserved.",
        ) from lookup_error
    if (
        pull_request is not None
        and pull_request.state == "open"
        and pull_request.draft
        and not pull_request.merged
        and pull_request.base_branch == base_branch
        and pull_request.head_branch == branch
        and pull_request.head_sha == commit_sha
        and observed_sha == commit_sha
    ):
        return DraftPullRequest(pull_request.number, pull_request.url)
    if pull_request is None and observed_sha == commit_sha:
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
        ) from original_error
    raise InfrastructureError(
        "github_pull_request_creation_uncertain",
        "GitHub publication state changed unexpectedly; the controlled branch was "
        "preserved for maintainer review.",
    ) from original_error


def _validate_context_identity(
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
    _validate_branch(base_branch)


def _validate_draft_pr_identity(report: Mapping[str, object]) -> None:
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
