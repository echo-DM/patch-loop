from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from patchloop.config import RepositoryConfig


class VerificationReport(TypedDict):
    status: Literal["not_run"]
    checks: list[object]


class PublicationIntent(TypedDict):
    intent: Literal["none"]
    reason: Literal["no_change"]


class RunReport(TypedDict):
    report_version: Literal["1"]
    task_id: str
    terminal_outcome: Literal["no_change"]
    summary: str
    actionable_message: str
    patch: None
    verification: VerificationReport
    publication: PublicationIntent
    errors: list[object]


@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    title: str
    body: str


@dataclass(frozen=True)
class RunRequest:
    task: TaskSnapshot
    repository: Path
    config: RepositoryConfig


@dataclass(frozen=True)
class RunResult:
    report: RunReport


def run(request: RunRequest) -> RunResult:
    """Execute a PatchLoop task through its stable black-box boundary."""
    _ = request.repository, request.config
    return RunResult(
        report={
            "report_version": "1",
            "task_id": request.task.id,
            "terminal_outcome": "no_change",
            "summary": "The repository already satisfies this task; no patch is needed.",
            "actionable_message": (
                "No files were changed and no pull request should be created."
            ),
            "patch": None,
            "verification": {"status": "not_run", "checks": []},
            "publication": {"intent": "none", "reason": "no_change"},
            "errors": [],
        }
    )
