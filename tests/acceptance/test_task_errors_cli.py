from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_invalid_task_uses_task_failure_exit_status(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURES / "no_change_repository", repository)
    task = tmp_path / "invalid-task.json"
    task.write_text(json.dumps({"title": "Missing an identifier", "body": "No ID"}))

    completed = subprocess.run(
        [
            "patchloop",
            "run",
            "--task",
            str(task),
            "--repository",
            str(repository),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "task",
            "code": "missing_task_field",
            "message": "Missing required task field: id.",
        }
    }
