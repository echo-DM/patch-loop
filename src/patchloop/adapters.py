from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol, Sequence, cast

from patchloop.config import RepositoryConfig


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
    terminal_outcome: Literal["no_change", "failed"]
    summary: str
    actionable_message: str
    errors: tuple[dict[str, str], ...] = ()


class TaskEvaluator(Protocol):
    def evaluate(
        self,
        task: EvaluationTask,
        repository: Path,
        config: RepositoryConfig,
    ) -> EvaluationDecision: ...


class GitHubClient(Protocol):
    def permission_for(self, repository: str, username: str) -> str | None: ...

    def issue_comments(
        self, repository: str, issue_number: int
    ) -> Sequence[Mapping[str, object]]: ...


@dataclass(frozen=True)
class NormalizedGitHubTask:
    id: str
    title: str
    body: str
    authorized_by: str
    supplemental_requirements: tuple[IssueComment, ...]
    reference_material: tuple[IssueComment, ...]


@dataclass(frozen=True)
class GateRejection:
    task_id: str
    code: str
    message: str


class GitHubIssueAdapter:
    """Normalize one authorized GitHub issues/labeled event into a frozen task."""

    def __init__(self, client: GitHubClient, target_label: str = "patchloop") -> None:
        self._client = client
        self._target_label = target_label

    def resolve(
        self, event: Mapping[str, object]
    ) -> NormalizedGitHubTask | GateRejection:
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
        if (
            not isinstance(issue_number, int)
            or not isinstance(title, str)
            or not isinstance(body, str)
        ):
            raise ValueError("GitHub issue number, title, and body are invalid.")
        try:
            permission = self._client.permission_for(repository, sender)
        except Exception:
            return GateRejection(
                task_id=f"github:{repository}#{issue_number}",
                code="permission_lookup_failed",
                message=f"Could not verify actor {sender}'s repository permission.",
            )
        if permission is not None and permission not in {"write", "admin"}:
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
                if author_permission in {"write", "admin"}
                else references
            )
            target.append(comment)

        return NormalizedGitHubTask(
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
