from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from conftest import FIXTURES, CliHarness
from patchloop import (
    GitHubEventTask,
    GitHubIssueAdapter,
    RunAdapters,
    RunRequest,
    RunResult,
    load_repository_config,
    run,
)
from patchloop.adapters import (
    DeterministicModelAdapter,
    EvaluationDecision,
    RepositoryPermission,
)


class FixtureGitHubClient:
    def __init__(
        self,
        permissions: dict[str, RepositoryPermission | None],
        comments: list[dict[str, object]] | None = None,
    ) -> None:
        self.permissions = permissions
        self.comments = comments or []

    def permission_for(
        self, repository: str, username: str
    ) -> RepositoryPermission | None:
        return self.permissions.get(username)

    def issue_comments(
        self, repository: str, issue_number: int
    ) -> list[dict[str, object]]:
        return self.comments


def authorized_event() -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((FIXTURES / "github" / "labeled_issue.json").read_text()),
    )


def run_model_decision(
    cli_harness: CliHarness,
    decision: EvaluationDecision,
    *,
    comments: list[dict[str, object]] | None = None,
    permissions: dict[str, RepositoryPermission | None] | None = None,
) -> tuple[RunResult, DeterministicModelAdapter]:
    repository = cli_harness.copy_repository()
    model = DeterministicModelAdapter({"github:octo-org/example#42": decision})
    client = FixtureGitHubClient(
        permissions or {"maintainer": "write"},
        comments,
    )
    result = run(
        RunRequest(
            task=GitHubEventTask(authorized_event()),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                evaluator=model,
                github=GitHubIssueAdapter(client),
            ),
        )
    )
    return result, model


def test_missing_reproduction_returns_bounded_sanitized_issue_feedback(
    cli_harness: CliHarness,
) -> None:
    secret = "ghp_clarification_secret"
    decision = EvaluationDecision(
        terminal_outcome="needs_clarification",
        summary=f"Missing reproduction; secret={secret}",
        actionable_message=f"Answer these questions; token={secret}",
        clarification_questions=(
            f"Which command reproduces the failure with token={secret}?",
            "What output did you expect, and what output did you observe?",
            "Which operating system and PatchLoop version are affected?",
        ),
    )

    result, _ = run_model_decision(cli_harness, decision)

    assert result.terminal_outcome == "needs_clarification"
    assert result.patch is None
    assert result.clarification_questions == (
        "Which command reproduces the failure with token=[REDACTED]?",
        "What output did you expect, and what output did you observe?",
        "Which operating system and PatchLoop version are affected?",
    )
    assert result.report["task_id"] == "github:octo-org/example#42"
    assert result.report["patch"] is None
    assert result.report["changed_files"] == {"count": 0, "paths": []}
    assert result.report["clarification_questions"] == list(
        result.clarification_questions
    )
    assert result.publication == {
        "intent": "issue_feedback",
        "reason": "needs_clarification",
        "questions": list(result.clarification_questions),
    }
    assert secret not in str(result.report)
    assert max(map(len, result.clarification_questions)) <= 240


def test_conflicting_requirements_return_specific_questions(
    cli_harness: CliHarness,
) -> None:
    result, _ = run_model_decision(
        cli_harness,
        EvaluationDecision(
            terminal_outcome="needs_clarification",
            summary="The requested compatibility ranges conflict.",
            actionable_message="Resolve the compatibility target before implementation.",
            clarification_questions=(
                "Should Python 3.11 remain supported, or may the minimum become 3.12?",
            ),
        ),
    )

    assert result.terminal_outcome == "needs_clarification"
    assert result.clarification_questions == (
        "Should Python 3.11 remain supported, or may the minimum become 3.12?",
    )
    assert result.publication["intent"] == "issue_feedback"


def test_trusted_comment_completes_clarification_context(
    cli_harness: CliHarness,
) -> None:
    comments = cast(
        list[dict[str, object]],
        json.loads((FIXTURES / "github" / "issue_comments.json").read_text()),
    )
    result, model = run_model_decision(
        cli_harness,
        EvaluationDecision(
            terminal_outcome="no_change",
            summary="The maintainer clarification confirms no edit is needed.",
            actionable_message="No pull request should be created.",
        ),
        comments=comments,
        permissions={
            "maintainer": "write",
            "reviewer": "admin",
            "external-user": "read",
        },
    )

    assert result.terminal_outcome == "no_change"
    assert result.clarification_questions == ()
    assert model.tasks[0].supplemental_requirements[0].author == "reviewer"
    assert model.tasks[0].reference_material[0].author == "external-user"
    assert result.publication == {"intent": "none", "reason": "no_change"}


def test_low_trust_comment_is_context_but_cannot_change_terminal_rules(
    cli_harness: CliHarness,
) -> None:
    comments = cast(
        list[dict[str, object]],
        json.loads((FIXTURES / "github" / "issue_comments.json").read_text()),
    )
    result, model = run_model_decision(
        cli_harness,
        EvaluationDecision(
            terminal_outcome="needs_clarification",
            summary="More information is required.",
            actionable_message="Describe the observed failure.",
            clarification_questions=("What exact failure did you observe?",),
        ),
        comments=comments,
        permissions={
            "maintainer": "write",
            "reviewer": "admin",
            "external-user": "read",
        },
    )

    assert model.tasks[0].reference_material[0].body.startswith("Ignore policy")
    assert "ghp_external_fixture_secret" not in str(model.tasks[0])
    assert result.terminal_outcome == "needs_clarification"
    assert result.publication["intent"] == "issue_feedback"


def test_empty_clarification_decision_fails_without_feedback_intent(
    cli_harness: CliHarness,
) -> None:
    result, _ = run_model_decision(
        cli_harness,
        EvaluationDecision(
            terminal_outcome="needs_clarification",
            summary="More information is required.",
            actionable_message="Answer a question.",
            clarification_questions=("   ",),
        ),
    )

    assert result.terminal_outcome == "failed"
    assert result.clarification_questions == ()
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.report["errors"][0]["code"] == "invalid_clarification_decision"


def test_oversized_clarification_question_fails_without_partial_feedback(
    cli_harness: CliHarness,
) -> None:
    result, _ = run_model_decision(
        cli_harness,
        EvaluationDecision(
            terminal_outcome="needs_clarification",
            summary="More detail is required.",
            actionable_message="Answer the question.",
            clarification_questions=(f"What does {'x' * 500} mean?",),
        ),
    )

    assert result.terminal_outcome == "failed"
    assert result.clarification_questions == ()
    assert result.publication == {"intent": "none", "reason": "failed"}


@pytest.mark.parametrize("declaration", ["No edit is required.", "No change is required."])
def test_explicit_no_change_remains_distinct_from_clarification(
    cli_harness: CliHarness,
    declaration: str,
) -> None:
    result, _ = run_model_decision(
        cli_harness,
        EvaluationDecision(
            terminal_outcome="no_change",
            summary=declaration,
            actionable_message="No pull request should be created.",
        ),
    )

    assert result.terminal_outcome == "no_change"
    assert result.clarification_questions == ()
    assert result.publication == {"intent": "none", "reason": "no_change"}
