from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Callable, Literal, Mapping, NotRequired, TypedDict, cast

from patchloop.adapters import (
    CheckResultDocument,
    EvaluationDecision,
    EvaluationTask,
    GateRejection,
    GitHubTaskResolver,
    IssueComment,
    PatchModel,
    PatchCompletion,
    ReportEvent,
    TaskEvaluator,
    TerminalOutcome,
    ToolResult,
    VerifierAdapter,
    VerificationResult,
    VerificationAttempt,
    verification_result_document,
)
from patchloop.config import RepositoryConfig
from patchloop.controlled_tools import (
    MAX_PATCHED_FILE_BYTES,
    ControlledTools,
    PatchBundle,
    diff_line_count,
)
from patchloop.errors import InfrastructureError
from patchloop.graph import evaluate_task
from patchloop.sanitize import redact_text


MAX_CLARIFICATION_QUESTIONS = 3
MAX_CLARIFICATION_QUESTION_CHARS = 240


class VerificationReport(TypedDict):
    status: Literal[
        "not_run",
        "checks_passed",
        "checks_failed",
        "setup_failed",
        "infrastructure_failed",
        "budget_exhausted",
    ]
    configured_checks: list[str]
    setup: list[CheckResultDocument]
    checks: list[CheckResultDocument]
    attempts: list[VerificationAttemptReport]


class VerificationAttemptReport(TypedDict):
    iteration: int
    patch_sha256: str | None
    status: Literal[
        "checks_passed", "checks_failed", "setup_failed", "infrastructure_failed"
    ]
    setup: list[CheckResultDocument]
    checks: list[CheckResultDocument]


class ChangedFilesReport(TypedDict):
    count: int
    paths: list[str]


class BudgetLimitsReport(TypedDict):
    max_iterations: int
    max_tool_calls: int
    max_changed_files: int
    max_diff_lines: int
    max_wall_time_minutes: int
    max_file_bytes: int


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


class ModelReport(TypedDict):
    provider: str
    name: str


class RunReport(TypedDict):
    report_version: Literal["1"]
    task_id: str
    model: ModelReport
    terminal_outcome: TerminalOutcome
    summary: str
    actionable_message: str
    patch: PatchReport | None
    changed_files: ChangedFilesReport
    verification: VerificationReport
    budgets: BudgetsReport
    publication: PublicationIntent
    errors: list[ReportEvent]
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
    clock: Callable[[], float] = monotonic


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
    started_at = request.adapters.clock()
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
                request.adapters.clock,
                started_at,
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
    elapsed_seconds = max(0.0, request.adapters.clock() - started_at)
    if elapsed_seconds >= request.config.budgets.max_wall_time_minutes * 60:
        return _wall_time_failure(task_id, request.config, elapsed_seconds)
    return _result_from_decision(
        task_id,
        decision,
        request.config,
        wall_time_minutes=int(elapsed_seconds // 60),
    )


def _result_from_decision(
    task_id: str,
    decision: EvaluationDecision,
    config: RepositoryConfig,
    *,
    wall_time_minutes: int = 0,
    budget_error: ReportEvent | None = None,
    model: object | None = None,
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
    verification = _verification_report(
        config, "budget_exhausted" if budget_error is not None else "not_run", []
    )
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
    errors: list[ReportEvent] = [
        {
            "category": error["category"],
            "code": error["code"],
            "message": redact_text(error["message"]),
        }
        for error in decision.errors
    ]
    if budget_error is not None:
        errors.append(budget_error)
    changed_files: ChangedFilesReport = {"count": 0, "paths": []}
    budgets: BudgetsReport = {
        "limits": _budget_limits(config),
        "usage": {
            "iterations": 0,
            "tool_calls": 0,
            "changed_files": 0,
            "diff_lines": 0,
            "wall_time_minutes": wall_time_minutes,
        },
        "resource_limit_events": [budget_error] if budget_error is not None else [],
    }
    report: RunReport = {
        "report_version": "1",
        "task_id": redact_text(task_id),
        "model": _model_report(config, model),
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
    clock: Callable[[], float],
    started_at: float,
) -> RunResult:
    tools = ControlledTools(repository, config.verifier, config.budgets, verifier)
    elapsed_seconds = 0.0

    def check_wall_time() -> ReportEvent | None:
        nonlocal elapsed_seconds
        elapsed_seconds = max(0.0, clock() - started_at)
        if elapsed_seconds >= config.budgets.max_wall_time_minutes * 60:
            return _wall_time_limit_error()
        return None

    observations: tuple[ToolResult, ...] = ()
    tool_calls = 0
    completion = None
    error: ReportEvent | None = None
    for _ in range(config.budgets.max_tool_calls + 1):
        if (error := check_wall_time()) is not None:
            break
        turn = model.next_turn(task, observations, tools.definitions)
        if (error := check_wall_time()) is not None:
            break
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
            return _result_from_decision(
                task.id,
                turn.decision,
                config,
                wall_time_minutes=int(elapsed_seconds // 60),
                model=model,
            )
        if turn.completion is not None:
            completion = turn.completion
            break
        if not turn.tool_calls:
            completion = _passing_checks_completion(tools)
            if completion is not None:
                break
            error = {
                "category": "model",
                "code": "empty_model_turn",
                "message": "The model must request a controlled tool or complete the task.",
            }
            break
        next_observations: list[ToolResult] = []
        for call in turn.tool_calls:
            if (error := check_wall_time()) is not None:
                break
            if tool_calls >= config.budgets.max_tool_calls:
                error = {
                    "category": "budget",
                    "code": "tool_call_limit_reached",
                    "message": "The configured controlled-tool call limit was reached.",
                }
                break
            tool_result = tools.execute(call)
            next_observations.append(tool_result)
            tool_calls += 1
            if (error := check_wall_time()) is not None:
                break
            if call.name == "run_checks" and tool_result.ok:
                error = _verifier_interruption(tools.latest_verification)
                if error is not None:
                    break
                completion = _passing_checks_completion(tools)
                if completion is not None:
                    break
            if tools.exhaustion_event is not None:
                error = tools.exhaustion_event
                break
        observations = tuple(next_observations)
        if error is not None or completion is not None:
            break

    patch = tools.patch_bundle()
    final_wall_error = check_wall_time()
    if error is None and final_wall_error is not None:
        error = final_wall_error
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
    if verification_result is not None and error is None:
        error = _verifier_interruption(verification_result)
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

    checks: list[CheckResultDocument] = []
    setup: list[CheckResultDocument] = []
    verification_status: Literal[
        "not_run",
        "checks_passed",
        "checks_failed",
        "setup_failed",
        "infrastructure_failed",
        "budget_exhausted",
    ] = "not_run"
    if verification_result is not None:
        verification_status = verification_result.status
        verification_document = verification_result_document(
            verification_result,
            max_output_bytes=config.verifier.limits.output_bytes,
        )
        setup = verification_document["setup"]
        checks = verification_document["checks"]
    budget_exhausted = error is not None and error["category"] == "budget"
    if budget_exhausted:
        verification_status = "budget_exhausted"
    verification = _verification_report(
        config,
        verification_status,
        checks,
        setup=setup,
        attempts=_verification_attempt_reports(tools.verification_attempts, config),
    )
    changed_paths = list(patch.changed_files) if patch is not None else []
    changed_files: ChangedFilesReport = {
        "count": len(changed_paths),
        "paths": changed_paths,
    }
    budgets: BudgetsReport = {
        "limits": _budget_limits(config),
        "usage": {
            "iterations": tools.iterations,
            "tool_calls": tool_calls,
            "changed_files": len(changed_paths),
            "diff_lines": diff_line_count(patch.content) if patch is not None else 0,
            "wall_time_minutes": int(elapsed_seconds // 60),
        },
        "resource_limit_events": list(tools.policy_events)
        + _verification_limit_events(verification_result)
        + ([error] if budget_exhausted and error not in tools.policy_events else []),
    }
    if error is None or (budget_exhausted and patch is not None):
        assert patch is not None
        publication: PublicationIntent = {
            "intent": "draft_pr",
            "reason": "patch_generated",
            "patch_sha256": patch.sha256,
        }
        terminal_outcome: TerminalOutcome = "pr_created"
        if error is None:
            assert completion is not None
            summary = completion.summary
            actionable_message = completion.actionable_message
            errors: list[ReportEvent] = []
        else:
            summary = "PatchLoop preserved a legal patch after reaching a budget."
            actionable_message = error["message"]
            errors = [error]
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
        "model": _model_report(config, model),
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


def _wall_time_limit_error() -> ReportEvent:
    return {
        "category": "budget",
        "code": "wall_time_limit_reached",
        "message": "The configured wall-clock time limit was reached.",
    }


def _model_report(config: RepositoryConfig, model: object | None) -> ModelReport:
    provider = getattr(model, "provider_name", "configured")
    if not isinstance(provider, str) or not provider:
        provider = "configured"
    return {"provider": redact_text(provider), "name": redact_text(config.model)}


def _verifier_interruption(
    verification: VerificationResult | None,
) -> ReportEvent | None:
    if verification is None:
        return None
    if verification.status == "setup_failed":
        return {
            "category": "configuration",
            "code": "verifier_setup_failed",
            "message": "The configured verifier setup did not complete successfully.",
        }
    if verification.status == "infrastructure_failed":
        return {
            "category": "infrastructure",
            "code": "verifier_infrastructure_failed",
            "message": "Docker could not complete the verifier run.",
        }
    return None


def _passing_checks_completion(tools: ControlledTools) -> PatchCompletion | None:
    if not tools.has_current_passing_verification():
        return None
    return PatchCompletion(
        "PatchLoop produced a patch that passes the configured checks.",
        "Review the generated Draft PR.",
    )


def _wall_time_failure(
    task_id: str, config: RepositoryConfig, elapsed_seconds: float
) -> RunResult:
    error = _wall_time_limit_error()
    return _result_from_decision(
        task_id,
        EvaluationDecision(
            terminal_outcome="failed",
            summary="PatchLoop stopped after reaching its total runtime budget.",
            actionable_message=error["message"],
        ),
        config,
        wall_time_minutes=int(elapsed_seconds // 60),
        budget_error=error,
    )


def _budget_limits(config: RepositoryConfig) -> BudgetLimitsReport:
    return {
        "max_iterations": config.budgets.max_iterations,
        "max_tool_calls": config.budgets.max_tool_calls,
        "max_changed_files": config.budgets.max_changed_files,
        "max_diff_lines": config.budgets.max_diff_lines,
        "max_wall_time_minutes": config.budgets.max_wall_time_minutes,
        "max_file_bytes": MAX_PATCHED_FILE_BYTES,
    }


def _verification_report(
    config: RepositoryConfig,
    status: Literal[
        "not_run",
        "checks_passed",
        "checks_failed",
        "setup_failed",
        "infrastructure_failed",
        "budget_exhausted",
    ],
    checks: list[CheckResultDocument],
    *,
    setup: list[CheckResultDocument] | None = None,
    attempts: list[VerificationAttemptReport] | None = None,
) -> VerificationReport:
    return {
        "status": status,
        "configured_checks": [
            redact_text(command) for command in config.verifier.checks
        ],
        "setup": setup or [],
        "checks": checks,
        "attempts": attempts or [],
    }


def _verification_attempt_reports(
    attempts: list[VerificationAttempt],
    config: RepositoryConfig,
) -> list[VerificationAttemptReport]:
    max_output_bytes = config.verifier.limits.output_bytes
    documents: list[VerificationAttemptReport] = []
    for iteration, attempt in enumerate(attempts, start=1):
        result = verification_result_document(
            attempt.result, max_output_bytes=max_output_bytes
        )
        documents.append(
            {
                "iteration": iteration,
                "patch_sha256": attempt.patch_sha256,
                "status": result["status"],
                "setup": result["setup"],
                "checks": result["checks"],
            }
        )
    return documents


def _verification_limit_events(
    verification: VerificationResult | None,
) -> list[ReportEvent]:
    if verification is None:
        return []
    messages = {
        "timeout": "timeout",
        "memory_limit": "memory",
        "process_limit": "process",
        "output_limit": "output",
    }
    events: list[ReportEvent] = []
    for result in (*verification.setup, *verification.checks):
        if result.failure_category not in messages:
            continue
        limit = messages[result.failure_category]
        events.append(
            {
                "category": "resource",
                "code": f"verifier_{result.failure_category}",
                "message": (
                    f"Verifier command {redact_text(result.command)} reached its "
                    f"{limit} limit."
                ),
            }
        )
    return events


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
