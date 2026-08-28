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
        "changed_files": {"count": 0, "paths": []},
        "verification": {
            "status": "not_run",
            "configured_checks": ["uv run pytest"],
            "checks": [],
        },
        "budgets": {
            "limits": {
                "max_iterations": 3,
                "max_tool_calls": 60,
                "max_changed_files": 20,
                "max_diff_lines": 2000,
                "max_wall_time_minutes": 30,
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
        "publication": {"intent": "none", "reason": "no_change"},
        "errors": [],
    }
    assert "ghp_fixture_secret" not in completed.stdout
