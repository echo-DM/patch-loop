from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parents[1] / "fixtures"


@dataclass(frozen=True)
class CliHarness:
    workspace: Path

    def copy_repository(self) -> Path:
        repository = self.workspace / "repository"
        shutil.copytree(FIXTURES / "no_change_repository", repository)
        return repository

    def write_task(self, document: dict[str, str], name: str = "task.json") -> Path:
        task = self.workspace / name
        task.write_text(json.dumps(document))
        return task

    def run(self, repository: Path, task: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "patchloop",
                "run",
                "--task",
                str(task or FIXTURES / "no_change_task.json"),
                "--repository",
                str(repository),
            ],
            check=False,
            capture_output=True,
            text=True,
        )


@pytest.fixture
def cli_harness(tmp_path: Path) -> CliHarness:
    return CliHarness(tmp_path)
