from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence, cast

from patchloop.config import RepositoryConfig


RepositoryPermission = Literal["admin", "write", "read", "none"]
TerminalOutcome = Literal["pr_created", "needs_clarification", "no_change", "failed"]
WRITE_PERMISSIONS: frozenset[RepositoryPermission] = frozenset({"admin", "write"})


def has_write_access(permission: RepositoryPermission | None) -> bool:
    return permission in WRITE_PERMISSIONS


@dataclass(frozen=True)
class IssueComment:
    id: int
    author: str
    body: str


@dataclass(frozen=True)
class EvaluationTask:
    id: str
    title: str
    body: str
    authorized_by: str | None = None
    supplemental_requirements: tuple[IssueComment, ...] = ()
    reference_material: tuple[IssueComment, ...] = ()


@dataclass(frozen=True)
class EvaluationDecision:
    terminal_outcome: TerminalOutcome
    summary: str
    actionable_message: str
    clarification_questions: tuple[str, ...] = ()
    errors: tuple[dict[str, str], ...] = ()


class TaskEvaluator(Protocol):
    def evaluate(
        self,
        task: EvaluationTask,
        repository: Path,
        config: RepositoryConfig,
    ) -> EvaluationDecision: ...


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    required_arguments: tuple[str, ...]
    optional_arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    output: object | None = None
    error: Mapping[str, str] | None = None


@dataclass(frozen=True)
class PatchCompletion:
    summary: str
    actionable_message: str


@dataclass(frozen=True)
class ModelTurn:
    tool_calls: tuple[ToolCall, ...] = ()
    completion: PatchCompletion | None = None
    decision: EvaluationDecision | None = None

    @classmethod
    def complete(cls, summary: str, actionable_message: str) -> ModelTurn:
        return cls(completion=PatchCompletion(summary, actionable_message))

    @classmethod
    def decide(cls, decision: EvaluationDecision) -> ModelTurn:
        return cls(decision=decision)


class PatchModel(Protocol):
    def next_turn(
        self,
        task: EvaluationTask,
        observations: tuple[ToolResult, ...],
        available_tools: tuple[ToolDefinition, ...],
    ) -> ModelTurn: ...


@dataclass(frozen=True)
class CheckResult:
    command: str
    status: Literal["passed", "failed"]
    exit_code: int
    output: str = ""


@dataclass(frozen=True)
class VerificationResult:
    status: Literal["checks_passed", "checks_failed"]
    checks: tuple[CheckResult, ...]

    @classmethod
    def passed(cls, commands: Sequence[str]) -> VerificationResult:
        return cls(
            status="checks_passed",
            checks=tuple(CheckResult(command, "passed", 0) for command in commands),
        )


@dataclass(frozen=True)
class VerificationRequest:
    repository: Path
    checks: tuple[str, ...]


class VerifierAdapter(Protocol):
    def verify(self, request: VerificationRequest) -> VerificationResult: ...


class DeterministicPatchModelAdapter:
    """Replay fixture model turns while recording the controlled-tool interface."""

    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)
        self.available_tools: tuple[ToolDefinition, ...] = ()
        self.observations: list[tuple[ToolResult, ...]] = []

    def next_turn(
        self,
        task: EvaluationTask,
        observations: tuple[ToolResult, ...],
        available_tools: tuple[ToolDefinition, ...],
    ) -> ModelTurn:
        _ = task
        self.available_tools = available_tools
        self.observations.append(observations)
        try:
            return next(self._turns)
        except StopIteration:
            return ModelTurn()


class DeterministicVerifierAdapter:
    """Return one fixture verification result without executing repository code."""

    def __init__(self, result: VerificationResult) -> None:
        self._result = result
        self.requests: list[VerificationRequest] = []

    def verify(self, request: VerificationRequest) -> VerificationResult:
        self.requests.append(request)
        return self._result


class DeterministicModelAdapter:
    """Return fixture decisions by task id without network or model credentials."""

    def __init__(self, decisions: Mapping[str, EvaluationDecision]) -> None:
        self._decisions = dict(decisions)
        self.tasks: list[EvaluationTask] = []

    def evaluate(
        self,
        task: EvaluationTask,
        repository: Path,
        config: RepositoryConfig,
    ) -> EvaluationDecision:
        _ = repository, config
        self.tasks.append(task)
        try:
            return self._decisions[task.id]
        except KeyError:
            message = f"No deterministic model decision is configured for task {task.id}."
            return EvaluationDecision(
                terminal_outcome="failed",
                summary="The deterministic model could not evaluate this task.",
                actionable_message="Configure a fixture decision for this task.",
                errors=(
                    {
                        "category": "model",
                        "code": "decision_not_configured",
                        "message": message,
                    },
                ),
            )


class GitHubClient(Protocol):
    def permission_for(
        self, repository: str, username: str
    ) -> RepositoryPermission | None: ...

    def issue_comments(
        self, repository: str, issue_number: int
    ) -> Sequence[Mapping[str, object]]: ...


@dataclass(frozen=True)
class GateRejection:
    task_id: str
    code: str
    message: str


class GitHubTaskResolver(Protocol):
    def resolve(
        self, event: Mapping[str, object]
    ) -> EvaluationTask | GateRejection: ...


class GitHubIssueAdapter:
    """Normalize one authorized GitHub issues/labeled event into a frozen task."""

    def __init__(self, client: GitHubClient, target_label: str = "patchloop") -> None:
        self._client = client
        self._target_label = target_label

    def resolve(
        self, event: Mapping[str, object]
    ) -> EvaluationTask | GateRejection:
        label = event.get("label")
        if (
            event.get("action") != "labeled"
            or not isinstance(label, dict)
            or label.get("name") != self._target_label
        ):
            return GateRejection(
                task_id="github-event",
                code="not_patchloop_trigger",
                message=(
                    f"Only adding the {self._target_label} label can trigger PatchLoop."
                ),
            )
        repository = cast(dict[str, object], event["repository"])["full_name"]
        sender = cast(dict[str, object], event["sender"])["login"]
        issue = cast(dict[str, object], event["issue"])
        if not isinstance(repository, str) or not isinstance(sender, str):
            raise ValueError("GitHub event repository and sender must be strings.")
        issue_number = issue["number"]
        title = issue["title"]
        body = issue["body"]
        updated_at = issue["updated_at"]
        if (
            not isinstance(issue_number, int)
            or not isinstance(title, str)
            or not isinstance(body, str)
            or not isinstance(updated_at, str)
        ):
            raise ValueError("GitHub issue snapshot fields are invalid.")
        snapshot_cutoff = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        try:
            permission = self._client.permission_for(repository, sender)
        except Exception:
            return GateRejection(
                task_id=f"github:{repository}#{issue_number}",
                code="permission_lookup_failed",
                message=f"Could not verify actor {sender}'s repository permission.",
            )
        if permission is not None and not has_write_access(permission):
            return GateRejection(
                task_id=f"github:{repository}#{issue_number}",
                code="insufficient_permission",
                message=(
                    f"Actor {sender} has {permission} permission; write or admin is "
                    "required."
                ),
            )
        if permission is None:
            return GateRejection(
                task_id=f"github:{repository}#{issue_number}",
                code="unknown_actor",
                message=f"Actor {sender} is not known to the repository.",
            )

        supplemental: list[IssueComment] = []
        references: list[IssueComment] = []
        for document in self._client.issue_comments(repository, issue_number):
            created_at = document["created_at"]
            comment_updated_at = document["updated_at"]
            if not isinstance(created_at, str) or not isinstance(comment_updated_at, str):
                raise ValueError("GitHub issue comment timestamps are invalid.")
            if max(
                datetime.fromisoformat(created_at.replace("Z", "+00:00")),
                datetime.fromisoformat(comment_updated_at.replace("Z", "+00:00")),
            ) > snapshot_cutoff:
                continue
            author = cast(dict[str, object], document["user"])["login"]
            comment_id = document["id"]
            comment_body = document["body"]
            if (
                not isinstance(author, str)
                or not isinstance(comment_id, int)
                or not isinstance(comment_body, str)
            ):
                raise ValueError("GitHub issue comment is invalid.")
            comment = IssueComment(comment_id, author, comment_body)
            try:
                author_permission = self._client.permission_for(repository, author)
            except Exception:
                author_permission = None
            target = (
                supplemental
                if has_write_access(author_permission)
                else references
            )
            target.append(comment)

        return EvaluationTask(
            id=f"github:{repository}#{issue_number}",
            title=title,
            body=body,
            authorized_by=sender,
            supplemental_requirements=tuple(supplemental),
            reference_material=tuple(references),
        )


class ExplicitNoChangeEvaluator:
    """Deterministic Ticket 01 adapter for explicitly no-change fixture tasks."""

    _DECLARATIONS = frozenset(
        {"no edit is required.", "no change is required."}
    )

    def evaluate(
        self,
        task: EvaluationTask,
        repository: Path,
        config: RepositoryConfig,
    ) -> EvaluationDecision:
        _ = repository, config
        declaration = " ".join(task.body.split()).casefold()
        if declaration in self._DECLARATIONS:
            return EvaluationDecision(
                terminal_outcome="no_change",
                summary="The task explicitly states that no repository change is required.",
                actionable_message=(
                    "No files were changed and no pull request should be created."
                ),
            )
        message = "Ticket 01 only evaluates tasks that explicitly require no change."
        return EvaluationDecision(
            terminal_outcome="failed",
            summary="The task may require repository changes and was not evaluated.",
            actionable_message=(
                "Use this tracer only for explicit no-change tasks; patch generation is "
                "implemented by a later ticket."
            ),
            errors=({"category": "task", "code": "unsupported_task", "message": message},),
        )
