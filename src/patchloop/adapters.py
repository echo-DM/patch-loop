from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from patchloop.config import RepositoryConfig


@dataclass(frozen=True)
class EvaluationTask:
    id: str
    title: str
    body: str


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
