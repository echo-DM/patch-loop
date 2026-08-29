from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, NotRequired, TypedDict, cast

from patchloop.adapters import (
    EvaluationDecision,
    EvaluationTask,
    GateRejection,
    GitHubTaskResolver,
    IssueComment,
    PatchModel,
    TaskEvaluator,
    TerminalOutcome,
    ToolResult,
    VerifierAdapter,
)
from patchloop.config import RepositoryConfig
from patchloop.controlled_tools import ControlledTools, PatchBundle
from patchloop.errors import InfrastructureError
from patchloop.graph import evaluate_task
from patchloop.sanitize import redact_text


MAX_CLARIFICATION_QUESTIONS = 3
MAX_CLARIFICATION_QUESTION_CHARS = 240


class VerificationReport(TypedDict):
    status: Literal["not_run", "checks_passed", "checks_failed"]
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


class NoPublicationIntent(TypedDict):
    intent: Literal["none"]
    reason: Literal["no_change", "failed"]


class IssueFeedbackIntent(TypedDict):
    intent: Literal["issue_feedback"]
    reason: Literal["needs_clarification"]
    questions: list[str]


class DraftPRIntent(TypedDict):
    intent: Literal["draft_pr"]
    reason: Literal["patch_generated"]
    patch_sha256: str


PublicationIntent = NoPublicationIntent | IssueFeedbackIntent | DraftPRIntent


class PatchReport(TypedDict):
    format: str
    sha256: str
    byte_length: int


class IntegrityReport(TypedDict):
    algorithm: Literal["sha256"]
    patch_sha256: str


class ReportError(TypedDict):
    category: str
    code: str
    message: str


class RunReport(TypedDict):
    report_version: Literal["1"]
    task_id: str
    terminal_outcome: TerminalOutcome
    summary: str
    actionable_message: str
    patch: PatchReport | None
    changed_files: ChangedFilesReport
    verification: VerificationReport
    budgets: BudgetsReport
    publication: PublicationIntent
    errors: list[ReportError]
    clarification_questions: NotRequired[list[str]]
    integrity: NotRequired[IntegrityReport]


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
    evaluator: TaskEvaluator | None = None
    github: GitHubTaskResolver | None = None
    model: PatchModel | None = None
    verifier: VerifierAdapter | None = None


@dataclass(frozen=True)
class RunRequest:
    task: TaskSnapshot | GitHubEventTask
    repository: Path
    config: RepositoryConfig
    adapters: RunAdapters


@dataclass(frozen=True)
class RunResult:
    terminal_outcome: TerminalOutcome
    patch: PatchBundle | None
    verification: VerificationReport
    report: RunReport
    publication: PublicationIntent
    clarification_questions: tuple[str, ...] = ()


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
        task = _sanitize_evaluation_task(task)
        if request.adapters.model is not None:
            if request.adapters.verifier is None:
                raise InfrastructureError(
                    "verifier_adapter_missing",
                    "A verifier adapter is required for controlled patch generation.",
                )
            return _run_patch(
                task,
                request.repository,
                request.config,
                request.adapters.model,
                request.adapters.verifier,
            )
        if request.adapters.evaluator is None:
            raise InfrastructureError(
                "model_adapter_missing",
                "A model or evaluator adapter is required to run PatchLoop.",
            )
        decision = evaluate_task(
            task,
            request.repository,
            request.config,
            request.adapters.evaluator,
        )
    return _result_from_decision(task_id, decision, request.config)


def _result_from_decision(
    task_id: str,
    decision: EvaluationDecision,
    config: RepositoryConfig,
) -> RunResult:
    questions = _validate_clarification_questions(decision)
    if decision.terminal_outcome == "pr_created":
        decision = EvaluationDecision(
            terminal_outcome="failed",
            summary="The evaluator returned an invalid terminal outcome.",
            actionable_message="Use the controlled patch model to create a patch.",
            errors=(
                {
                    "category": "model",
                    "code": "invalid_evaluation_decision",
                    "message": "Evaluator adapters cannot create a PR outcome.",
                },
            ),
        )
        questions = ()
    if decision.terminal_outcome == "needs_clarification" and questions is None:
        decision = EvaluationDecision(
            terminal_outcome="failed",
            summary="The model returned an invalid clarification decision.",
            actionable_message=(
                "Retry the run after the model can provide at least one concrete question."
            ),
            errors=(
                {
                    "category": "model",
                    "code": "invalid_clarification_decision",
                    "message": (
                        "A clarification decision must contain one to three complete "
                        "questions of at most 240 characters each."
                    ),
                },
            ),
        )
        questions = ()
    assert questions is not None
    verification = _verification_report(config, "not_run", [])
    publication: PublicationIntent
    if decision.terminal_outcome == "needs_clarification":
        publication = {
            "intent": "issue_feedback",
            "reason": "needs_clarification",
            "questions": list(questions),
        }
    else:
        publication = {
            "intent": "none",
            "reason": cast(Literal["no_change", "failed"], decision.terminal_outcome),
        }
    errors: list[ReportError] = [
        {
            "category": error["category"],
            "code": error["code"],
            "message": redact_text(error["message"]),
        }
        for error in decision.errors
    ]
    changed_files: ChangedFilesReport = {"count": 0, "paths": []}
    budgets: BudgetsReport = {
        "limits": _budget_limits(config),
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
        "task_id": redact_text(task_id),
        "terminal_outcome": decision.terminal_outcome,
        "summary": redact_text(decision.summary),
        "actionable_message": redact_text(decision.actionable_message),
        "patch": None,
        "changed_files": changed_files,
        "verification": verification,
        "budgets": budgets,
        "publication": publication,
        "errors": errors,
    }
    if decision.terminal_outcome == "needs_clarification":
        report["clarification_questions"] = list(questions)
    return RunResult(
        terminal_outcome=decision.terminal_outcome,
        patch=None,
        verification=verification,
        report=report,
        publication=publication,
        clarification_questions=questions,
    )


def _run_patch(
    task: EvaluationTask,
    repository: Path,
    config: RepositoryConfig,
    model: PatchModel,
    verifier: VerifierAdapter,
) -> RunResult:
    tools = ControlledTools(repository, config.verifier.checks, verifier)
    observations: tuple[ToolResult, ...] = ()
    tool_calls = 0
    completion = None
    error: ReportError | None = None
    for _ in range(config.budgets.max_tool_calls + 1):
        turn = model.next_turn(task, observations, tools.definitions)
        selected_actions = sum(
            (
                bool(turn.tool_calls),
                turn.completion is not None,
                turn.decision is not None,
            )
        )
        if selected_actions > 1:
            error = {
                "category": "model",
                "code": "invalid_model_turn",
                "message": "A model turn must select exactly one kind of action.",
            }
            break
        if turn.decision is not None:
            if tools.patch_bundle() is not None:
                error = {
                    "category": "model",
                    "code": "decision_after_edit",
                    "message": "A no-patch decision cannot follow repository edits.",
                }
                break
            return _result_from_decision(task.id, turn.decision, config)
        if turn.completion is not None:
            completion = turn.completion
            break
        if not turn.tool_calls:
            error = {
                "category": "model",
                "code": "empty_model_turn",
                "message": "The model must request a controlled tool or complete the task.",
            }
            break
        next_observations: list[ToolResult] = []
        for call in turn.tool_calls:
            if tool_calls >= config.budgets.max_tool_calls:
                error = {
                    "category": "budget",
                    "code": "tool_call_limit_reached",
                    "message": "The configured controlled-tool call limit was reached.",
                }
                break
            next_observations.append(tools.execute(call))
            tool_calls += 1
        observations = tuple(next_observations)
        if error is not None:
            break

    patch = tools.patch_bundle()
    verification_result = tools.latest_verification
    if completion is None and error is None:
        error = {
            "category": "model",
            "code": "model_did_not_complete",
            "message": "The model did not complete within the controlled execution limit.",
        }
    if patch is None and error is None:
        error = {
            "category": "model",
            "code": "empty_patch",
            "message": "Patch generation completed without a repository change.",
        }
    if verification_result is None and error is None:
        error = {
            "category": "verification",
            "code": "checks_not_requested",
            "message": "A generated patch must be checked through the verifier adapter.",
        }
    if (
        verification_result is not None
        and patch is not None
        and tools.verified_patch_sha256 != patch.sha256
        and error is None
    ):
        error = {
            "category": "verification",
            "code": "patch_changed_after_verification",
            "message": "The final patch differs from the patch sent to the verifier.",
        }

    checks: list[object] = []
    verification_status: Literal["not_run", "checks_passed", "checks_failed"] = (
        "not_run"
    )
    if verification_result is not None:
        verification_status = verification_result.status
        checks = [
            {
                "command": redact_text(check.command),
                "status": check.status,
                "exit_code": check.exit_code,
                "output": redact_text(check.output),
            }
            for check in verification_result.checks
        ]
    verification = _verification_report(config, verification_status, checks)
    changed_paths = list(patch.changed_files) if patch is not None else []
    changed_files: ChangedFilesReport = {
        "count": len(changed_paths),
        "paths": changed_paths,
    }
    budgets: BudgetsReport = {
        "limits": _budget_limits(config),
        "usage": {
            "iterations": 1 if patch is not None else 0,
            "tool_calls": tool_calls,
            "changed_files": len(changed_paths),
            "diff_lines": _diff_line_count(patch.content) if patch is not None else 0,
            "wall_time_minutes": 0,
        },
        "resource_limit_events": [],
    }
    if error is None:
        assert completion is not None
        assert patch is not None
        publication: PublicationIntent = {
            "intent": "draft_pr",
            "reason": "patch_generated",
            "patch_sha256": patch.sha256,
        }
        terminal_outcome: TerminalOutcome = "pr_created"
        summary = completion.summary
        actionable_message = completion.actionable_message
        errors: list[ReportError] = []
    else:
        publication = {"intent": "none", "reason": "failed"}
        terminal_outcome = "failed"
        summary = "PatchLoop could not produce a safe publishable patch."
        actionable_message = error["message"]
        errors = [error]
        patch = None
    patch_report: PatchReport | None = None
    if patch is not None:
        patch_report = {
            "format": patch.format,
            "sha256": patch.sha256,
            "byte_length": patch.byte_length,
        }
    report: RunReport = {
        "report_version": "1",
        "task_id": redact_text(task.id),
        "terminal_outcome": terminal_outcome,
        "summary": redact_text(summary),
        "actionable_message": redact_text(actionable_message),
        "patch": patch_report,
        "changed_files": changed_files,
        "verification": verification,
        "budgets": budgets,
        "publication": publication,
        "errors": errors,
    }
    if patch is not None:
        report["integrity"] = {
            "algorithm": "sha256",
            "patch_sha256": patch.sha256,
        }
    return RunResult(
        terminal_outcome=terminal_outcome,
        patch=patch,
        verification=verification,
        report=report,
        publication=publication,
    )


def _diff_line_count(content: str) -> int:
    return sum(
        1
        for line in content.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    )


def _budget_limits(config: RepositoryConfig) -> BudgetLimitsReport:
    return {
        "max_iterations": config.budgets.max_iterations,
        "max_tool_calls": config.budgets.max_tool_calls,
        "max_changed_files": config.budgets.max_changed_files,
        "max_diff_lines": config.budgets.max_diff_lines,
        "max_wall_time_minutes": config.budgets.max_wall_time_minutes,
    }


def _verification_report(
    config: RepositoryConfig,
    status: Literal["not_run", "checks_passed", "checks_failed"],
    checks: list[object],
) -> VerificationReport:
    return {
        "status": status,
        "configured_checks": [
            redact_text(command) for command in config.verifier.checks
        ],
        "checks": checks,
    }


def _sanitize_evaluation_task(task: EvaluationTask) -> EvaluationTask:
    def sanitize_comment(comment: IssueComment) -> IssueComment:
        return IssueComment(
            id=comment.id,
            author=redact_text(comment.author),
            body=redact_text(comment.body),
        )

    return EvaluationTask(
        id=task.id,
        title=redact_text(task.title),
        body=redact_text(task.body),
        authorized_by=(
            redact_text(task.authorized_by) if task.authorized_by is not None else None
        ),
        supplemental_requirements=tuple(
            sanitize_comment(comment) for comment in task.supplemental_requirements
        ),
        reference_material=tuple(
            sanitize_comment(comment) for comment in task.reference_material
        ),
    )


def _validate_clarification_questions(
    decision: EvaluationDecision,
) -> tuple[str, ...] | None:
    if decision.terminal_outcome != "needs_clarification":
        return ()
    if not 1 <= len(decision.clarification_questions) <= MAX_CLARIFICATION_QUESTIONS:
        return None
    questions: list[str] = []
    for raw_question in decision.clarification_questions:
        question = redact_text(" ".join(raw_question.split())).strip()
        if (
            not question
            or len(question) > MAX_CLARIFICATION_QUESTION_CHARS
            or not question.endswith(("?", "？"))
        ):
            return None
        questions.append(question)
    return tuple(questions)
