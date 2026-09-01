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
    branch_heads: list[str | None] = field(default_factory=lambda: [None])
    base_mode: Literal["100644", "100755"] = "100644"

    def create_issue_comment(
        self, repository: str, issue_number: int, body: str
    ) -> None:
        self.requests.append(("create_issue_comment", repository, issue_number, body))

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
    assert publisher.requests[:3] == [
        ("base_revision", "octo-org/example", "main"),
        (
            "base_files",
            "octo-org/example",
            BaseRevision(commit_sha="base-sha", tree_sha="tree-sha"),
            ("README.md",),
        ),
        ("branch_head", "octo-org/example", "patchloop/issue-42"),
    ]
    assert publisher.requests[3] == (
        "create_commit",
        "octo-org/example",
        BaseRevision(commit_sha="base-sha", tree_sha="tree-sha"),
        (CommitFile("README.md", b"Hello from PatchLoop!\n", "100644"),),
        "patchloop: address issue #42",
    )
    assert publisher.requests[4] == (
        "create_branch",
        "octo-org/example",
        "patchloop/issue-42",
        "commit-sha",
    )
    pr_request = publisher.requests[5]
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
    publisher = FixturePublisher([], fail_pr=True)

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
    publisher = FixturePublisher([], fail_pr=True, fail_delete=True)

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
