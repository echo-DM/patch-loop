from __future__ import annotations

import json

from conftest import CliHarness


def test_invalid_task_uses_task_failure_exit_status(cli_harness: CliHarness) -> None:
    repository = cli_harness.copy_repository()
    task = cli_harness.write_task(
        {"title": "Missing an identifier", "body": "No ID"}, "invalid-task.json"
    )
    completed = cli_harness.run(repository, task)

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "task",
            "code": "missing_task_field",
            "message": "Missing required task field: id.",
        }
    }


def test_change_request_is_not_falsely_reported_as_no_change(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    task = cli_harness.write_task(
        {"id": "issue-102", "title": "Change the greeting", "body": "Edit README."},
        "change-task.json",
    )
    completed = cli_harness.run(repository, task)

    report = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert report["terminal_outcome"] == "failed"
    assert report["task_id"] == "issue-102"
    assert report["errors"] == [
        {
            "category": "task",
            "code": "unsupported_task",
            "message": "Ticket 01 only evaluates tasks that explicitly require no change.",
        }
    ]


def test_unavailable_repository_uses_infrastructure_exit_status(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.workspace / "not-a-repository"
    repository.write_text("not a directory")

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "infrastructure",
            "code": "invalid_repository",
            "message": f"Repository workspace is not a directory: {repository}.",
        }
    }
