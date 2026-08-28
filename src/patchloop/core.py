from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict

from patchloop.adapters import EvaluationTask, TaskEvaluator
from patchloop.config import RepositoryConfig
from patchloop.errors import InfrastructureError


class VerificationReport(TypedDict):
    status: Literal["not_run"]
    checks: list[object]


class PublicationIntent(TypedDict):
    intent: Literal["none"]
    reason: Literal["no_change", "failed"]


class ReportError(TypedDict):
    category: str
    code: str
    message: str


class RunReport(TypedDict):
    report_version: Literal["1"]
    task_id: str
    terminal_outcome: Literal["no_change", "failed"]
    summary: str
    actionable_message: str
    patch: None
    verification: VerificationReport
    publication: PublicationIntent
    errors: list[ReportError]


@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    title: str
    body: str


@dataclass(frozen=True)
class RunAdapters:
    evaluator: TaskEvaluator


@dataclass(frozen=True)
class RunRequest:
    task: TaskSnapshot
    repository: Path
    config: RepositoryConfig
    adapters: RunAdapters


@dataclass(frozen=True)
class RunResult:
    terminal_outcome: Literal["no_change", "failed"]
    patch: None
    verification: VerificationReport
    report: RunReport
    publication: PublicationIntent


def run(request: RunRequest) -> RunResult:
    """Execute a PatchLoop task through its stable black-box boundary."""
    if not request.repository.is_dir():
        raise InfrastructureError(
            "invalid_repository",
            f"Repository workspace is not a directory: {request.repository}.",
        )
    decision = request.adapters.evaluator.evaluate(
        EvaluationTask(
            id=request.task.id,
            title=request.task.title,
            body=request.task.body,
        ),
        request.repository,
        request.config,
    )
    verification: VerificationReport = {"status": "not_run", "checks": []}
    publication: PublicationIntent = {
        "intent": "none",
        "reason": decision.terminal_outcome,
    }
    errors: list[ReportError] = [
        {
            "category": error["category"],
            "code": error["code"],
            "message": error["message"],
        }
        for error in decision.errors
    ]
    report: RunReport = {
        "report_version": "1",
        "task_id": request.task.id,
        "terminal_outcome": decision.terminal_outcome,
        "summary": decision.summary,
        "actionable_message": decision.actionable_message,
        "patch": None,
        "verification": verification,
        "publication": publication,
        "errors": errors,
    }
    return RunResult(
        terminal_outcome=decision.terminal_outcome,
        patch=None,
        verification=verification,
        report=report,
        publication=publication,
    )
