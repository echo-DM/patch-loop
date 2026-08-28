from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, TypedDict

from patchloop.adapters import (
    EvaluationDecision,
    EvaluationTask,
    GateRejection,
    GitHubTaskResolver,
    TaskEvaluator,
)
from patchloop.config import RepositoryConfig
from patchloop.errors import InfrastructureError
from patchloop.sanitize import redact_text


class VerificationReport(TypedDict):
    status: Literal["not_run"]
    configured_checks: list[str]
    checks: list[object]


class ChangedFilesReport(TypedDict):
    count: int
    paths: list[str]


class BudgetLimitsReport(TypedDict):
    max_iterations: int
    max_tool_calls: int
    max_changed_files: int
    max_diff_lines: int
    max_wall_time_minutes: int


class BudgetUsageReport(TypedDict):
    iterations: int
    tool_calls: int
    changed_files: int
    diff_lines: int
    wall_time_minutes: int


class BudgetsReport(TypedDict):
    limits: BudgetLimitsReport
    usage: BudgetUsageReport
    resource_limit_events: list[object]


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
    changed_files: ChangedFilesReport
    verification: VerificationReport
    budgets: BudgetsReport
    publication: PublicationIntent
    errors: list[ReportError]


@dataclass(frozen=True)
class TaskSnapshot:
    id: str
    title: str
    body: str


@dataclass(frozen=True)
class GitHubEventTask:
    document: Mapping[str, object]


@dataclass(frozen=True)
class RunAdapters:
    evaluator: TaskEvaluator
    github: GitHubTaskResolver | None = None


@dataclass(frozen=True)
class RunRequest:
    task: TaskSnapshot | GitHubEventTask
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


def validate_repository_workspace(repository: Path) -> None:
    if not repository.is_dir():
        raise InfrastructureError(
            "invalid_repository",
            f"Repository workspace is not a directory: {repository}.",
        )


def run(request: RunRequest) -> RunResult:
    """Execute a PatchLoop task through its stable black-box boundary."""
    validate_repository_workspace(request.repository)
    decision: EvaluationDecision | None = None
    if isinstance(request.task, GitHubEventTask):
        if request.adapters.github is None:
            raise InfrastructureError(
                "github_adapter_missing",
                "A GitHub adapter is required for GitHub event tasks.",
            )
        resolution = request.adapters.github.resolve(request.task.document)
        if isinstance(resolution, GateRejection):
            task = None
            task_id = resolution.task_id
            decision = EvaluationDecision(
                terminal_outcome="failed",
                summary="The GitHub event was not authorized to run PatchLoop.",
                actionable_message=resolution.message,
                errors=(
                    {
                        "category": "authorization",
                        "code": resolution.code,
                        "message": resolution.message,
                    },
                ),
            )
        else:
            task = resolution
            task_id = task.id
    else:
        task = EvaluationTask(
            id=request.task.id,
            title=request.task.title,
            body=request.task.body,
        )
        task_id = task.id
    if decision is None:
        assert task is not None
        decision = request.adapters.evaluator.evaluate(
            task,
            request.repository,
            request.config,
        )
    verification: VerificationReport = {
        "status": "not_run",
        "configured_checks": [
            redact_text(command) for command in request.config.verifier.checks
        ],
        "checks": [],
    }
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
    changed_files: ChangedFilesReport = {"count": 0, "paths": []}
    budgets: BudgetsReport = {
        "limits": {
            "max_iterations": request.config.budgets.max_iterations,
            "max_tool_calls": request.config.budgets.max_tool_calls,
            "max_changed_files": request.config.budgets.max_changed_files,
            "max_diff_lines": request.config.budgets.max_diff_lines,
            "max_wall_time_minutes": request.config.budgets.max_wall_time_minutes,
        },
        "usage": {
            "iterations": 0,
            "tool_calls": 0,
            "changed_files": 0,
            "diff_lines": 0,
            "wall_time_minutes": 0,
        },
        "resource_limit_events": [],
    }
    report: RunReport = {
        "report_version": "1",
        "task_id": task_id,
        "terminal_outcome": decision.terminal_outcome,
        "summary": decision.summary,
        "actionable_message": decision.actionable_message,
        "patch": None,
        "changed_files": changed_files,
        "verification": verification,
        "budgets": budgets,
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
