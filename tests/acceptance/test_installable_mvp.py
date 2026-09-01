from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import yaml


ROOT = Path(__file__).parents[2]
EXAMPLE_CONFIG = ROOT / "examples" / "patchloop.yml"
EXAMPLE_TASK = ROOT / "examples" / "local-task.json"
SMOKE_CALLER = ROOT / "examples" / "patchloop-smoke-caller.yml"


def test_documented_local_example_runs_through_the_installed_cli(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    repository.joinpath("README.md").write_text("Hello from the target repository.\n")
    shutil.copyfile(EXAMPLE_CONFIG, repository / ".patchloop.yml")

    completed = subprocess.run(
        [
            "patchloop",
            "run",
            "--task",
            str(EXAMPLE_TASK),
            "--repository",
            str(repository),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    report = json.loads(completed.stdout)
    assert report["report_version"] == "1"
    assert report["task_id"] == "local-no-change"
    assert report["terminal_outcome"] == "no_change"
    assert report["publication"] == {"intent": "none", "reason": "no_change"}
    assert report["verification"]["configured_checks"] == [
        "uv run mypy",
        "uv run pytest",
    ]


def test_live_local_reproduction_requires_the_dedicated_gemini_key(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    repository.joinpath("README.md").write_text("Hello from the target repository.\n")
    shutil.copyfile(EXAMPLE_CONFIG, repository / ".patchloop.yml")
    environment = os.environ.copy()
    environment.pop("PATCHLOOP_GEMINI_API_KEY", None)

    completed = subprocess.run(
        [
            "patchloop",
            "run",
            "--live",
            "--task",
            str(EXAMPLE_TASK),
            "--repository",
            str(repository),
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "gemini_api_key_missing",
            "message": (
                "Set PATCHLOOP_GEMINI_API_KEY to use the Gemini adapter."
            ),
        }
    }


def test_live_smoke_template_uses_a_candidate_sha_and_read_only_inspection() -> None:
    workflow = cast(
        dict[str, object], yaml.load(SMOKE_CALLER.read_text(), Loader=yaml.BaseLoader)
    )
    jobs = cast(dict[str, object], workflow["jobs"])
    patchloop = cast(dict[str, object], jobs["patchloop"])
    inspection = cast(dict[str, object], jobs["inspect-live-state"])

    assert patchloop["uses"] == (
        "echo-DM/patch-loop/.github/workflows/patchloop-reusable.yml@"
        "REPLACE_WITH_CANDIDATE_SHA"
    )
    assert patchloop["secrets"] == {
        "gemini_api_key": "${{ secrets.PATCHLOOP_GEMINI_API_KEY }}"
    }
    assert inspection["needs"] == "patchloop"
    assert inspection["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
    }
    inspection_text = json.dumps(inspection, sort_keys=True)
    assert "PATCHLOOP_RUN_GITHUB_SMOKE" in inspection_text
    assert "PATCHLOOP_GEMINI_API_KEY" not in inspection_text
    assert "github.token" in inspection_text
