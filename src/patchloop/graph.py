from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from patchloop.adapters import EvaluationDecision, EvaluationTask, TaskEvaluator
from patchloop.config import RepositoryConfig
from patchloop.sanitize import redact_text


class EvaluationState(TypedDict):
    decision: NotRequired[EvaluationDecision]


def evaluate_task(
    task: EvaluationTask,
    repository: Path,
    config: RepositoryConfig,
    evaluator: TaskEvaluator,
) -> EvaluationDecision:
    """Run task evaluation inside the private LangGraph state machine."""

    def evaluate_node(state: EvaluationState) -> dict[str, EvaluationDecision]:
        _ = state
        decision = evaluator.evaluate(task, repository, config)
        return {
            "decision": EvaluationDecision(
                terminal_outcome=decision.terminal_outcome,
                summary=redact_text(decision.summary),
                actionable_message=redact_text(decision.actionable_message),
                clarification_questions=tuple(
                    redact_text(question)
                    for question in decision.clarification_questions
                ),
                errors=tuple(
                    {
                        "category": redact_text(error["category"]),
                        "code": redact_text(error["code"]),
                        "message": redact_text(error["message"]),
                    }
                    for error in decision.errors
                ),
            ),
        }

    builder = StateGraph(EvaluationState)
    builder.add_node("evaluate", evaluate_node)
    builder.add_edge(START, "evaluate")
    builder.add_edge("evaluate", END)
    final_state = builder.compile().invoke({})
    return cast(EvaluationDecision, final_state["decision"])
