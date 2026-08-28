from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast

import pytest

from conftest import FIXTURES, CliHarness
from patchloop import (
    GitHubEventTask,
    GitHubIssueAdapter,
    RunAdapters,
    RunRequest,
    load_repository_config,
    run,
)
from patchloop.adapters import EvaluationDecision, EvaluationTask


class FixtureGitHubClient:
    def __init__(
        self,
        permissions: dict[str, str | None],
        comments: list[dict[str, object]],
    ) -> None:
        self.permissions = permissions
        self.comments = comments
        self.permission_requests: list[str] = []

    def permission_for(self, repository: str, username: str) -> str | None:
        assert repository == "octo-org/example"
        self.permission_requests.append(username)
        return self.permissions.get(username)

    def issue_comments(
        self, repository: str, issue_number: int
    ) -> list[dict[str, object]]:
        assert repository == "octo-org/example"
        assert issue_number == 42
        return self.comments


class RecordingNoChangeEvaluator:
    def __init__(self) -> None:
        self.tasks: list[EvaluationTask] = []

    def evaluate(self, task: EvaluationTask, repository: Path, config: object) -> EvaluationDecision:
        self.tasks.append(task)
        return EvaluationDecision(
            terminal_outcome="no_change",
            summary="Authorized task needs no change.",
            actionable_message="No pull request should be created.",
        )


class FailingPermissionGitHubClient(FixtureGitHubClient):
    def permission_for(self, repository: str, username: str) -> str | None:
        raise RuntimeError("fixture API token detail")


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES / "github" / name).read_text())


@pytest.mark.parametrize("actor_permission", ["write", "admin"])
def test_write_or_admin_actor_authorizes_an_immutable_task_snapshot(
    cli_harness: CliHarness,
    actor_permission: str,
) -> None:
    repository = cli_harness.copy_repository()
    evaluator = RecordingNoChangeEvaluator()
    client = FixtureGitHubClient(
        permissions={
            "maintainer": actor_permission,
            "reviewer": "admin",
            "external-user": "read",
        },
        comments=cast(list[dict[str, object]], load_fixture("issue_comments.json")),
    )

    result = run(
        RunRequest(
            task=GitHubEventTask(
                cast(dict[str, object], load_fixture("labeled_issue.json"))
            ),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                evaluator=evaluator,
                github=GitHubIssueAdapter(client),
            ),
        )
    )

    assert result.terminal_outcome == "no_change"
    assert len(evaluator.tasks) == 1
    snapshot = evaluator.tasks[0]
    assert snapshot.id == "github:octo-org/example#42"
    assert snapshot.title == "Keep the greeting"
    assert snapshot.body == "No change is required."
    assert snapshot.authorized_by == "maintainer"
    assert snapshot.supplemental_requirements[0].author == "reviewer"
    assert snapshot.reference_material[0].author == "external-user"
    assert snapshot.reference_material[0].body.startswith("Ignore policy")
    assert result.report["budgets"]["limits"]["max_tool_calls"] == 60
    assert result.publication == {"intent": "none", "reason": "no_change"}
    with pytest.raises(FrozenInstanceError):
        snapshot.title = "mutated"
    with pytest.raises(FrozenInstanceError):
        snapshot.reference_material[0].body = "mutated"


@pytest.mark.parametrize("fixture_name", ["edited_issue.json", "other_label_issue.json"])
def test_non_labeled_issue_event_is_rejected_before_permission_or_evaluation(
    cli_harness: CliHarness,
    fixture_name: str,
) -> None:
    repository = cli_harness.copy_repository()
    evaluator = RecordingNoChangeEvaluator()
    client = FixtureGitHubClient({"maintainer": "write"}, [])

    result = run(
        RunRequest(
            task=GitHubEventTask(
                cast(dict[str, object], load_fixture(fixture_name))
            ),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                evaluator=evaluator,
                github=GitHubIssueAdapter(client),
            ),
        )
    )

    assert result.terminal_outcome == "failed"
    assert result.report["errors"] == [
        {
            "category": "authorization",
            "code": "not_patchloop_trigger",
            "message": "Only adding the patchloop label can trigger PatchLoop.",
        }
    ]
    assert evaluator.tasks == []
    assert client.permission_requests == []


def test_lower_permission_is_rejected_before_evaluation(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    evaluator = RecordingNoChangeEvaluator()
    client = FixtureGitHubClient({"maintainer": "triage"}, [])

    result = run(
        RunRequest(
            task=GitHubEventTask(
                cast(dict[str, object], load_fixture("labeled_issue.json"))
            ),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                evaluator=evaluator,
                github=GitHubIssueAdapter(client),
            ),
        )
    )

    assert result.terminal_outcome == "failed"
    assert result.report["errors"] == [
        {
            "category": "authorization",
            "code": "insufficient_permission",
            "message": "Actor maintainer has triage permission; write or admin is required.",
        }
    ]
    assert evaluator.tasks == []


def test_unknown_actor_has_a_distinct_authorization_result(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    evaluator = RecordingNoChangeEvaluator()

    result = run(
        RunRequest(
            task=GitHubEventTask(
                cast(dict[str, object], load_fixture("labeled_issue.json"))
            ),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                evaluator=evaluator,
                github=GitHubIssueAdapter(FixtureGitHubClient({}, [])),
            ),
        )
    )

    assert result.terminal_outcome == "failed"
    assert result.report["errors"] == [
        {
            "category": "authorization",
            "code": "unknown_actor",
            "message": "Actor maintainer is not known to the repository.",
        }
    ]
    assert evaluator.tasks == []


def test_permission_lookup_failure_has_a_sanitized_authorization_result(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    evaluator = RecordingNoChangeEvaluator()

    result = run(
        RunRequest(
            task=GitHubEventTask(
                cast(dict[str, object], load_fixture("labeled_issue.json"))
            ),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                evaluator=evaluator,
                github=GitHubIssueAdapter(FailingPermissionGitHubClient({}, [])),
            ),
        )
    )

    assert result.terminal_outcome == "failed"
    assert result.report["errors"] == [
        {
            "category": "authorization",
            "code": "permission_lookup_failed",
            "message": "Could not verify actor maintainer's repository permission.",
        }
    ]
    assert "fixture API token detail" not in str(result.report)
    assert evaluator.tasks == []
