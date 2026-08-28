from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_no_change_task_returns_sanitized_report(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURES / "no_change_repository", repository)

    completed = subprocess.run(
        [
            "patchloop",
            "run",
            "--task",
            str(FIXTURES / "no_change_task.json"),
            "--repository",
            str(repository),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "report_version": "1",
        "task_id": "issue-101",
        "terminal_outcome": "no_change",
        "summary": "The repository already satisfies this task; no patch is needed.",
        "actionable_message": "No files were changed and no pull request should be created.",
        "patch": None,
        "verification": {"status": "not_run", "checks": []},
        "publication": {"intent": "none", "reason": "no_change"},
        "errors": [],
    }
    assert "ghp_fixture_secret" not in completed.stdout
