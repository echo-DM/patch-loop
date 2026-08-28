from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_unknown_config_version_is_a_configuration_error(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURES / "no_change_repository", repository)
    config = repository / ".patchloop.yml"
    config.write_text(config.read_text().replace("version: 1", "version: 2"))

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

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "unsupported_config_version",
            "message": "Unsupported configuration version 2; expected 1.",
        }
    }


def test_missing_required_config_field_is_named(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURES / "no_change_repository", repository)
    config = repository / ".patchloop.yml"
    config.write_text(config.read_text().replace("model: gemini-2.5-flash\n", ""))

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

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "missing_required_field",
            "message": "Missing required configuration field: model.",
        }
    }


def test_budget_above_safety_ceiling_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    shutil.copytree(FIXTURES / "no_change_repository", repository)
    config = repository / ".patchloop.yml"
    config.write_text(config.read_text().replace("max_tool_calls: 60", "max_tool_calls: 61"))

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

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "unsafe_budget_value",
            "message": "budgets.max_tool_calls must be between 1 and 60; got 61.",
        }
    }
