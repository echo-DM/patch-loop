from __future__ import annotations

import json

from conftest import CliHarness


def test_no_change_task_returns_sanitized_report(cli_harness: CliHarness) -> None:
    completed = cli_harness.run(cli_harness.copy_repository())

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "report_version": "1",
        "task_id": "issue-101",
        "terminal_outcome": "no_change",
        "summary": "The task explicitly states that no repository change is required.",
        "actionable_message": "No files were changed and no pull request should be created.",
        "patch": None,
        "verification": {"status": "not_run", "checks": []},
        "publication": {"intent": "none", "reason": "no_change"},
        "errors": [],
    }
    assert "ghp_fixture_secret" not in completed.stdout
