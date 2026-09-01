from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Literal

import pytest

from patchloop.errors import InfrastructureError
from patchloop.github_publish import (
    BaseFile,
    BaseRevision,
    CommitFile,
    DraftPullRequest,
    PullRequestState,
    run_publish,
)
from patchloop.workflow_artifacts import (
    ArtifactIntegrityError,
    json_bytes,
    write_artifact,
)


PATCH = """\
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello, world!
+Hello from PatchLoop!
"""


@dataclass
class FixturePublisher:
    requests: list[tuple[object, ...]]
    fail_pr: bool = False
    fail_delete: bool = False
    fail_branch: bool = False
    fail_update_branch: bool = False
    fail_update_pr: bool = False
    branch_heads: list[str | None] = field(default_factory=lambda: [None])
    base_mode: Literal["100644", "100755"] = "100644"
    pull_request: PullRequestState | None = None
    pull_requests: list[PullRequestState | None] | None = None

    def create_issue_comment(
        self, repository: str, issue_number: int, body: str
    ) -> None:
        self.requests.append(("create_issue_comment", repository, issue_number, body))

    def pull_request_for_branch(
        self, repository: str, branch: str
    ) -> PullRequestState | None:
        self.requests.append(("pull_request_for_branch", repository, branch))
        if self.pull_requests is not None:
            return self.pull_requests.pop(0)
        return self.pull_request

    def base_revision(self, repository: str, branch: str) -> BaseRevision:
        self.requests.append(("base_revision", repository, branch))
        return BaseRevision(commit_sha="base-sha", tree_sha="tree-sha")

    def base_files(
        self,
        repository: str,
        base: BaseRevision,
        paths: tuple[str, ...],
    ) -> tuple[BaseFile, ...]:
        self.requests.append(("base_files", repository, base, paths))
        return tuple(
            BaseFile(path, b"Hello, world!\n", self.base_mode) for path in paths
        )

    def branch_head(self, repository: str, branch: str) -> str | None:
        self.requests.append(("branch_head", repository, branch))
        return self.branch_heads.pop(0)

    def create_commit(
        self,
        repository: str,
        base: BaseRevision,
        files: tuple[CommitFile, ...],
        message: str,
    ) -> str:
        self.requests.append(("create_commit", repository, base, files, message))
        return "commit-sha"

    def create_branch(self, repository: str, branch: str, commit_sha: str) -> None:
        self.requests.append(("create_branch", repository, branch, commit_sha))
        if self.fail_branch:
            raise RuntimeError("fixture branch failure")

    def update_branch(
        self, repository: str, branch: str, commit_sha: str
    ) -> None:
        self.requests.append(("update_branch", repository, branch, commit_sha, False))
        if self.fail_update_branch:
            raise RuntimeError("fixture branch update failure")

    def update_pull_request(
        self, repository: str, number: int, body: str
    ) -> DraftPullRequest:
        self.requests.append(("update_pull_request", repository, number, body))
        if self.fail_update_pr:
            raise RuntimeError("fixture PR update failure")
        return DraftPullRequest(number=number, url="https://example.test/pull/7")

    def create_draft_pull_request(
        self,
        repository: str,
        *,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
    ) -> DraftPullRequest:
        self.requests.append(
            (
                "create_draft_pull_request",
                repository,
                base_branch,
                head_branch,
                title,
                body,
            )
        )
        if self.fail_pr:
            raise RuntimeError("fixture PR failure")
        return DraftPullRequest(number=7, url="https://example.test/pull/7")

    def delete_branch(self, repository: str, branch: str) -> None:
        self.requests.append(("delete_branch", repository, branch))
        if self.fail_delete:
            raise RuntimeError("fixture delete failure")


def test_valid_checked_patch_creates_controlled_branch_commit_and_draft_pr(
    tmp_path: Path,
) -> None:
    artifact = checked_artifact(tmp_path, "checks_passed")
    publisher = FixturePublisher([])

    result = run_publish(
        artifact=artifact,
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result == DraftPullRequest(number=7, url="https://example.test/pull/7")
    assert publisher.requests[:4] == [
        (
            "pull_request_for_branch",
            "octo-org/example",
            "patchloop/issue-42",
        ),
        ("branch_head", "octo-org/example", "patchloop/issue-42"),
        ("base_revision", "octo-org/example", "main"),
        (
            "base_files",
            "octo-org/example",
            BaseRevision(commit_sha="base-sha", tree_sha="tree-sha"),
            ("README.md",),
        ),
    ]
    assert publisher.requests[4] == (
        "create_commit",
        "octo-org/example",
        BaseRevision(commit_sha="base-sha", tree_sha="tree-sha"),
        (CommitFile("README.md", b"Hello from PatchLoop!\n", "100644"),),
        "patchloop: address issue #42",
    )
    assert publisher.requests[5] == (
        "create_branch",
        "octo-org/example",
        "patchloop/issue-42",
        "commit-sha",
    )
    pr_request = publisher.requests[6]
    assert pr_request[:5] == (
        "create_draft_pull_request",
        "octo-org/example",
        "main",
        "patchloop/issue-42",
        "PatchLoop: address #42",
    )
    body = str(pr_request[5])
    assert "Draft" in body
    assert "PatchLoop checks passed" in body
    assert "README.md" in body
    assert "check greeting" in body
    assert "1 / 3" in body
    assert "Relates to #42" in body
    assert "not a substitute for the repository's complete CI" in body


def test_active_draft_pr_appends_an_ordinary_commit_to_its_current_branch(
    tmp_path: Path,
) -> None:
    active = PullRequestState(
        number=7,
        url="https://example.test/pull/7",
        state="open",
        merged=False,
        draft=True,
        base_branch="main",
        head_branch="patchloop/issue-42",
        head_sha="active-sha",
        mergeable=True,
    )
    publisher = FixturePublisher(
        [],
        pull_request=active,
        branch_heads=["active-sha"],
    )

    result = run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result == DraftPullRequest(7, "https://example.test/pull/7")
    assert publisher.requests[:4] == [
        ("pull_request_for_branch", "octo-org/example", "patchloop/issue-42"),
        ("branch_head", "octo-org/example", "patchloop/issue-42"),
        ("base_revision", "octo-org/example", "patchloop/issue-42"),
        (
            "base_files",
            "octo-org/example",
            BaseRevision("base-sha", "tree-sha"),
            ("README.md",),
        ),
    ]
    assert publisher.requests[4] == (
        "create_commit",
        "octo-org/example",
        BaseRevision("base-sha", "tree-sha"),
        (CommitFile("README.md", b"Hello from PatchLoop!\n", "100644"),),
        "patchloop: address issue #42",
    )
    assert publisher.requests[5] == (
        "update_branch",
        "octo-org/example",
        "patchloop/issue-42",
        "commit-sha",
        False,
    )
    assert publisher.requests[6][0:3] == (
        "update_pull_request",
        "octo-org/example",
        7,
    )
    assert not any(request[0] == "create_branch" for request in publisher.requests)
    assert not any(
        request[0] == "create_draft_pull_request" for request in publisher.requests
    )


def test_merged_pr_marks_automatic_work_complete_without_recreating_it(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [],
        pull_request=pull_request_state(state="closed", merged=True),
    )

    result = run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result is None
    assert publisher.requests == [
        ("pull_request_for_branch", "octo-org/example", "patchloop/issue-42")
    ]


def test_closed_unmerged_pr_requires_an_explicit_restart_without_writes(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [],
        pull_request=pull_request_state(state="closed", merged=False),
    )

    result = run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result is None
    assert [request[0] for request in publisher.requests] == [
        "pull_request_for_branch",
        "create_issue_comment",
    ]
    assert "closed without merging" in str(publisher.requests[-1][3])
    assert "explicit restart" in str(publisher.requests[-1][3])


def test_conflicted_active_pr_is_reported_without_changing_the_branch(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [],
        pull_request=pull_request_state(mergeable=False),
        branch_heads=["active-sha"],
    )

    result = run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result is None
    assert [request[0] for request in publisher.requests] == [
        "pull_request_for_branch",
        "branch_head",
        "create_issue_comment",
    ]
    assert "merge conflict" in str(publisher.requests[-1][3])
    assert not any(request[0] == "create_commit" for request in publisher.requests)


@pytest.mark.parametrize(
    ("base_branch", "head_branch", "head_sha", "branch_head"),
    [
        ("release", "patchloop/issue-42", "active-sha", "active-sha"),
        ("main", "other-branch", "active-sha", "active-sha"),
        ("main", "patchloop/issue-42", "active-sha", "other-sha"),
    ],
)
def test_incompatible_active_pr_is_reported_without_automatic_history_changes(
    tmp_path: Path,
    base_branch: str,
    head_branch: str,
    head_sha: str,
    branch_head: str,
) -> None:
    publisher = FixturePublisher(
        [],
        pull_request=pull_request_state(
            base_branch=base_branch,
            head_branch=head_branch,
            head_sha=head_sha,
        ),
        branch_heads=[branch_head],
    )

    result = run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result is None
    assert publisher.requests[-1][0] == "create_issue_comment"
    assert "incompatible" in str(publisher.requests[-1][3])
    assert not any(request[0] == "create_commit" for request in publisher.requests)


def test_branch_update_accepted_before_transport_failure_continues_safely(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [],
        fail_update_branch=True,
        pull_request=pull_request_state(),
        branch_heads=["active-sha", "commit-sha"],
    )

    result = run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result == DraftPullRequest(7, "https://example.test/pull/7")
    assert [request[0] for request in publisher.requests[-3:]] == [
        "update_branch",
        "branch_head",
        "update_pull_request",
    ]


def test_failed_branch_update_preserves_the_existing_pr_and_reports_failure(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [],
        fail_update_branch=True,
        pull_request=pull_request_state(),
        branch_heads=["active-sha", "active-sha"],
    )

    with pytest.raises(InfrastructureError) as captured:
        run_publish(
            artifact=checked_artifact(tmp_path, "checks_passed"),
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
        )

    assert captured.value.code == "github_branch_update_failed"
    assert not any(request[0] == "delete_branch" for request in publisher.requests)
    assert not any(
        request[0] == "update_pull_request" for request in publisher.requests
    )


def test_pr_body_partial_failure_keeps_the_appended_commit_and_existing_pr(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [],
        fail_update_pr=True,
        pull_request=pull_request_state(),
        branch_heads=["active-sha"],
    )

    with pytest.raises(InfrastructureError) as captured:
        run_publish(
            artifact=checked_artifact(tmp_path, "checks_passed"),
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
        )

    assert captured.value.code == "github_pull_request_update_failed"
    assert any(request[0] == "update_branch" for request in publisher.requests)
    assert not any(request[0] == "delete_branch" for request in publisher.requests)


@pytest.mark.parametrize(
    ("status", "expected", "unexpected"),
    [
        ("checks_failed", "checks failed", "checks passed"),
        ("budget_exhausted", "budget was exhausted", "checks passed"),
    ],
)
def test_partial_patch_statuses_remain_draft_and_are_not_confused(
    tmp_path: Path, status: str, expected: str, unexpected: str
) -> None:
    publisher = FixturePublisher([])

    run_publish(
        artifact=checked_artifact(tmp_path, status),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    body = str(publisher.requests[-1][5])
    assert expected in body
    assert unexpected not in body
    assert "remains a Draft" in body
    assert "approved" in body
    assert "merged" in body


def test_pull_request_failure_removes_the_new_controlled_branch(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [], fail_pr=True, branch_heads=[None, "commit-sha"]
    )

    with pytest.raises(InfrastructureError) as captured:
        run_publish(
            artifact=checked_artifact(tmp_path, "checks_passed"),
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
        )

    assert captured.value.code == "github_pull_request_failed"
    assert publisher.requests[-1] == (
        "delete_branch",
        "octo-org/example",
        "patchloop/issue-42",
    )


def test_draft_pr_created_before_transport_failure_is_kept(
    tmp_path: Path,
) -> None:
    created = pull_request_state(head_sha="commit-sha")
    publisher = FixturePublisher(
        [],
        fail_pr=True,
        branch_heads=[None, "commit-sha"],
        pull_requests=[None, created],
    )

    result = run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert result == DraftPullRequest(7, "https://example.test/pull/7")
    assert [request[0] for request in publisher.requests[-3:]] == [
        "create_draft_pull_request",
        "pull_request_for_branch",
        "branch_head",
    ]
    assert not any(request[0] == "delete_branch" for request in publisher.requests)


def test_existing_executable_mode_is_preserved_in_the_commit(tmp_path: Path) -> None:
    publisher = FixturePublisher([], base_mode="100755")

    run_publish(
        artifact=checked_artifact(tmp_path, "checks_passed"),
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    commit_request = next(
        request for request in publisher.requests if request[0] == "create_commit"
    )
    assert commit_request[3] == (
        CommitFile("README.md", b"Hello from PatchLoop!\n", "100755"),
    )


def test_branch_creation_accepted_before_transport_failure_is_rolled_back(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher(
        [], fail_branch=True, branch_heads=[None, "commit-sha"]
    )

    with pytest.raises(InfrastructureError) as captured:
        run_publish(
            artifact=checked_artifact(tmp_path, "checks_passed"),
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
        )

    assert captured.value.code == "github_branch_creation_failed"
    assert publisher.requests[-1] == (
        "delete_branch",
        "octo-org/example",
        "patchloop/issue-42",
    )


def test_rollback_failure_has_a_stable_error(tmp_path: Path) -> None:
    publisher = FixturePublisher(
        [], fail_pr=True, fail_delete=True, branch_heads=[None, "commit-sha"]
    )

    with pytest.raises(InfrastructureError) as captured:
        run_publish(
            artifact=checked_artifact(tmp_path, "checks_passed"),
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
        )

    assert captured.value.code == "github_publish_rollback_failed"


@pytest.mark.parametrize(
    ("repository", "issue_number", "base_branch"),
    [
        ("octo-org/other", 42, "main"),
        ("octo-org/example", 41, "main"),
        ("octo-org/example", 42, "main..malicious"),
    ],
)
def test_identity_and_base_are_rejected_before_any_github_request(
    tmp_path: Path, repository: str, issue_number: int, base_branch: str
) -> None:
    publisher = FixturePublisher([])

    with pytest.raises(ArtifactIntegrityError):
        run_publish(
            artifact=checked_artifact(tmp_path, "checks_passed"),
            repository=repository,
            issue_number=issue_number,
            base_branch=base_branch,
            client=publisher,
        )

    assert publisher.requests == []


def test_protected_patch_path_is_rejected_before_reading_or_writing_github(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher([])
    protected_patch = """\
--- a/.github/workflows/unsafe.yml
+++ b/.github/workflows/unsafe.yml
@@ -0,0 +1 @@
+run: unsafe
"""

    with pytest.raises(ArtifactIntegrityError):
        run_publish(
            artifact=checked_artifact(
                tmp_path,
                "checks_passed",
                patch=protected_patch,
                changed_path=".github/workflows/unsafe.yml",
            ),
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
        )

    assert publisher.requests == []


def test_invalid_pr_text_is_rejected_before_summary_or_github_write(
    tmp_path: Path,
) -> None:
    publisher = FixturePublisher([])
    summary_path = tmp_path / "summary.md"

    with pytest.raises(ArtifactIntegrityError):
        run_publish(
            artifact=checked_artifact(
                tmp_path, "checks_passed", report_summary=["not", "text"]
            ),
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
            summary=summary_path,
        )

    assert not summary_path.exists()
    assert not any(request[0] == "create_commit" for request in publisher.requests)


def checked_artifact(
    tmp_path: Path,
    status: str,
    *,
    patch: str = PATCH,
    changed_path: str = "README.md",
    report_summary: object = "Updated the greeting.",
) -> Path:
    artifact = tmp_path / "result"
    patch_hash = hashlib.sha256(patch.encode()).hexdigest()
    publication = {
        "intent": "draft_pr",
        "reason": "patch_generated",
        "patch_sha256": patch_hash,
    }
    report = {
        "report_version": "1",
        "task_id": "github:octo-org/example#42",
        "model": {"provider": "fixture", "name": "fixture-model"},
        "terminal_outcome": "pr_created",
        "summary": report_summary,
        "actionable_message": "Review the draft.",
        "patch": {
            "format": "unified_diff",
            "sha256": patch_hash,
            "byte_length": len(patch.encode()),
        },
        "changed_files": {"count": 1, "paths": [changed_path]},
        "verification": {
            "status": status,
            "configured_checks": ["check greeting"],
            "setup": [],
            "checks": [
                {
                    "command": "check greeting",
                    "status": "passed" if status == "checks_passed" else "failed",
                    "exit_code": 0 if status == "checks_passed" else 1,
                    "output": "",
                    "failure_category": None,
                    "output_truncated": False,
                }
            ],
            "attempts": [],
        },
        "budgets": {
            "limits": {
                "max_iterations": 3,
                "max_tool_calls": 60,
                "max_changed_files": 20,
                "max_diff_lines": 2000,
                "max_wall_time_minutes": 30,
                "max_file_bytes": 1_000_000,
            },
            "usage": {
                "iterations": 1,
                "tool_calls": 2,
                "changed_files": 1,
                "diff_lines": 2,
                "wall_time_minutes": 0,
            },
            "resource_limit_events": [],
        },
        "publication": publication,
        "errors": [],
        "integrity": {"algorithm": "sha256", "patch_sha256": patch_hash},
    }
    write_artifact(
        artifact,
        {
            "patch.diff": patch.encode(),
            "publication-context.json": json_bytes(
                {
                    "context_version": "1",
                    "task_id": "github:octo-org/example#42",
                    "repository": "octo-org/example",
                    "issue_number": 42,
                    "base_branch": "main",
                }
            ),
            "publication-intent.json": json_bytes(publication),
            "run-report.json": json_bytes(report),
        },
    )
    return artifact


def pull_request_state(
    *,
    state: Literal["open", "closed"] = "open",
    merged: bool = False,
    base_branch: str = "main",
    head_branch: str = "patchloop/issue-42",
    head_sha: str = "active-sha",
    mergeable: bool | None = True,
) -> PullRequestState:
    return PullRequestState(
        number=7,
        url="https://example.test/pull/7",
        state=state,
        merged=merged,
        draft=True,
        base_branch=base_branch,
        head_branch=head_branch,
        head_sha=head_sha,
        mergeable=mergeable,
    )
