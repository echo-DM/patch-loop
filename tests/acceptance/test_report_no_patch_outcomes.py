from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from patchloop.errors import InfrastructureError
from patchloop.github_publish import DraftPullRequest, run_publish
from patchloop.workflow_artifacts import json_bytes, write_artifact


@dataclass
class FeedbackPublisher:
    requests: list[tuple[object, ...]] = field(default_factory=list)

    def create_issue_comment(
        self, repository: str, issue_number: int, body: str
    ) -> None:
        self.requests.append(("create_issue_comment", repository, issue_number, body))

    def base_revision(self, repository: str, branch: str) -> object:
        self.requests.append(("base_revision", repository, branch))
        raise AssertionError("no-patch publication must not read a Git base")

    def base_files(self, *args: object) -> tuple[object, ...]:
        self.requests.append(("base_files", *args))
        raise AssertionError("no-patch publication must not read Git files")

    def branch_head(self, repository: str, branch: str) -> str | None:
        self.requests.append(("branch_head", repository, branch))
        raise AssertionError("no-patch publication must not inspect a branch")

    def create_commit(self, *args: object) -> str:
        self.requests.append(("create_commit", *args))
        raise AssertionError("no-patch publication must not create a commit")

    def create_branch(self, *args: object) -> None:
        self.requests.append(("create_branch", *args))
        raise AssertionError("no-patch publication must not create a branch")

    def create_draft_pull_request(
        self, *args: object, **kwargs: object
    ) -> DraftPullRequest:
        self.requests.append(("create_draft_pull_request", *args, kwargs))
        raise AssertionError("no-patch publication must not create a pull request")

    def delete_branch(self, *args: object) -> None:
        self.requests.append(("delete_branch", *args))
        raise AssertionError("no-patch publication must not delete a branch")


def test_no_change_explains_the_result_without_creating_a_branch_or_pr(
    tmp_path: Path,
) -> None:
    artifact = no_patch_artifact(
        tmp_path,
        terminal_outcome="no_change",
        publication={"intent": "none", "reason": "no_change"},
        summary="The requested behavior already exists.",
        actionable_message="No code modification is required.",
    )
    publisher = FeedbackPublisher()
    workflow_summary = tmp_path / "summary.md"

    result = run_publish(
        artifact=artifact,
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
        summary=workflow_summary,
    )

    assert result is None
    assert len(publisher.requests) == 1
    operation, repository, issue_number, body = publisher.requests[0]
    assert (operation, repository, issue_number) == (
        "create_issue_comment",
        "octo-org/example",
        42,
    )
    assert "No code change needed" in str(body)
    assert "The requested behavior already exists." in str(body)
    assert "No branch or pull request was created." in str(body)
    assert "no_change" in workflow_summary.read_text()


def test_clarification_posts_only_bounded_questions_and_retrigger_instructions(
    tmp_path: Path,
) -> None:
    artifact = no_patch_artifact(
        tmp_path,
        terminal_outcome="needs_clarification",
        publication={
            "intent": "issue_feedback",
            "reason": "needs_clarification",
            "questions": ["Which Python versions must remain supported?"],
        },
        summary="More information is required.",
        actionable_message="Answer the question before retrying.",
        clarification_questions=["Which Python versions must remain supported?"],
    )
    publisher = FeedbackPublisher()

    run_publish(
        artifact=artifact,
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    body = str(publisher.requests[0][3])
    assert "Needs clarification" in body
    assert "Which Python versions must remain supported?" in body
    assert "remove and re-add the `patchloop` label" in body
    assert len(publisher.requests) == 1


@pytest.mark.parametrize(
    ("errors", "verification_status", "expected_heading"),
    [
        (
            [
                {
                    "category": "authorization",
                    "code": "insufficient_permission",
                    "message": "denied",
                }
            ],
            "not_run",
            "Run not authorized",
        ),
        (
            [
                {
                    "category": "configuration",
                    "code": "invalid_config_value",
                    "message": "invalid",
                }
            ],
            "not_run",
            "Configuration error",
        ),
        (
            [
                {
                    "category": "model",
                    "code": "provider_error",
                    "message": "provider failed",
                }
            ],
            "not_run",
            "Model error",
        ),
        (
            [
                {
                    "category": "configuration",
                    "code": "verifier_setup_failed",
                    "message": "setup failed",
                }
            ],
            "setup_failed",
            "Docker or setup error",
        ),
        (
            [
                {
                    "category": "infrastructure",
                    "code": "verifier_infrastructure_failed",
                    "message": "docker failed",
                }
            ],
            "infrastructure_failed",
            "Docker or setup error",
        ),
        (
            [
                {
                    "category": "budget",
                    "code": "tool_call_limit_reached",
                    "message": "limit reached",
                }
            ],
            "budget_exhausted",
            "Budget exhausted without a legal patch",
        ),
    ],
)
def test_failed_no_patch_outcomes_use_distinct_templates_and_never_touch_git(
    tmp_path: Path,
    errors: list[dict[str, object]],
    verification_status: str,
    expected_heading: str,
) -> None:
    artifact = no_patch_artifact(
        tmp_path,
        terminal_outcome="failed",
        publication={"intent": "none", "reason": "failed"},
        summary="PatchLoop could not publish a patch.",
        actionable_message="Follow the reported action and retry.",
        errors=errors,
        verification_status=verification_status,
    )
    publisher = FeedbackPublisher()

    run_publish(
        artifact=artifact,
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    assert len(publisher.requests) == 1
    assert publisher.requests[0][0] == "create_issue_comment"
    assert expected_heading in str(publisher.requests[0][3])
    assert "No branch or pull request was created." in str(publisher.requests[0][3])


def test_feedback_redacts_truncates_and_escapes_untrusted_report_text(
    tmp_path: Path,
) -> None:
    secret = "ghp_credentialshaped123456789"
    malicious = f"[click](javascript:alert(1)) <details>{secret}</details> " + ("x" * 900)
    artifact = no_patch_artifact(
        tmp_path,
        terminal_outcome="no_change",
        publication={"intent": "none", "reason": "no_change"},
        summary=malicious,
        actionable_message="No edit is required.",
    )
    publisher = FeedbackPublisher()

    run_publish(
        artifact=artifact,
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    body = str(publisher.requests[0][3])
    assert secret not in body
    assert "REDACTED" in body
    assert "\\[click\\]\\(javascript:alert\\(1\\)\\)" in body
    assert "<details>" not in body
    assert len(body) < 1_200


def test_feedback_redacts_bearer_and_aws_credential_shapes(tmp_path: Path) -> None:
    bearer = "eyJhbGciOiJIUzI1NiJ9.private.signature"
    aws_access_key = "AKIAIOSFODNN7EXAMPLE"
    aws_secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    artifact = no_patch_artifact(
        tmp_path,
        terminal_outcome="no_change",
        publication={"intent": "none", "reason": "no_change"},
        summary=(
            f"Authorization: Bearer {bearer}; access id {aws_access_key}; "
            f"AWS_SECRET_ACCESS_KEY={aws_secret}"
        ),
        actionable_message="No edit is required.",
    )
    publisher = FeedbackPublisher()

    run_publish(
        artifact=artifact,
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    body = str(publisher.requests[0][3])
    assert bearer not in body
    assert aws_access_key not in body
    assert aws_secret not in body
    assert body.count("REDACTED") >= 3


def test_low_permission_feedback_does_not_repeat_actor_repository_or_secret_state(
    tmp_path: Path,
) -> None:
    artifact = no_patch_artifact(
        tmp_path,
        terminal_outcome="failed",
        publication={"intent": "none", "reason": "failed"},
        summary="private-org/private-repo",
        actionable_message=(
            "Actor outsider has read access; PATCHLOOP_GEMINI_API_KEY is missing."
        ),
        errors=[
            {
                "category": "authorization",
                "code": "insufficient_permission",
                "message": "Actor outsider only has read permission.",
            }
        ],
    )
    publisher = FeedbackPublisher()

    run_publish(
        artifact=artifact,
        repository="octo-org/example",
        issue_number=42,
        base_branch="main",
        client=publisher,
    )

    body = str(publisher.requests[0][3])
    assert "Run not authorized" in body
    assert "outsider" not in body
    assert "private-org" not in body
    assert "GEMINI" not in body
    assert "missing" not in body


def test_issue_write_failure_keeps_summary_and_returns_one_stable_failure(
    tmp_path: Path,
) -> None:
    artifact = no_patch_artifact(
        tmp_path,
        terminal_outcome="no_change",
        publication={"intent": "none", "reason": "no_change"},
        summary="No edit is required.",
        actionable_message="Nothing needs to be published.",
    )
    publisher = FeedbackPublisher()

    def fail_once(repository: str, issue_number: int, body: str) -> None:
        publisher.requests.append(
            ("create_issue_comment", repository, issue_number, body)
        )
        raise RuntimeError("ghp_internaltransportsecret123456")

    publisher.create_issue_comment = fail_once  # type: ignore[method-assign]
    workflow_summary = tmp_path / "summary.md"

    with pytest.raises(InfrastructureError) as captured:
        run_publish(
            artifact=artifact,
            repository="octo-org/example",
            issue_number=42,
            base_branch="main",
            client=publisher,
            summary=workflow_summary,
        )

    assert captured.value.code == "github_issue_feedback_failed"
    assert len(publisher.requests) == 1
    summary = workflow_summary.read_text()
    assert "no_change" in summary
    assert "github_issue_feedback_failed" in summary
    assert "internaltransportsecret" not in summary


def no_patch_artifact(
    tmp_path: Path,
    *,
    terminal_outcome: str,
    publication: dict[str, object],
    summary: str,
    actionable_message: str,
    clarification_questions: list[str] | None = None,
    errors: list[dict[str, object]] | None = None,
    verification_status: str = "not_run",
) -> Path:
    artifact = tmp_path / "result"
    report = {
        "report_version": "1",
        "task_id": "github:octo-org/example#42",
        "model": {"provider": "fixture", "name": "fixture-model"},
        "terminal_outcome": terminal_outcome,
        "summary": summary,
        "actionable_message": actionable_message,
        "patch": None,
        "changed_files": {"count": 0, "paths": []},
        "verification": {
            "status": verification_status,
            "configured_checks": [],
            "setup": [],
            "checks": [],
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
                "iterations": 0,
                "tool_calls": 0,
                "changed_files": 0,
                "diff_lines": 0,
                "wall_time_minutes": 0,
            },
            "resource_limit_events": [],
        },
        "publication": publication,
        "errors": errors or [],
    }
    if clarification_questions is not None:
        report["clarification_questions"] = clarification_questions
    write_artifact(
        artifact,
        {
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
