from __future__ import annotations

from pathlib import Path
from typing import NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from patchloop.adapters import EvaluationDecision, EvaluationTask, TaskEvaluator
from patchloop.config import RepositoryConfig


class EvaluationState(TypedDict):
    task: EvaluationTask
    decision: NotRequired[EvaluationDecision]


def evaluate_task(
    task: EvaluationTask,
    repository: Path,
    config: RepositoryConfig,
    evaluator: TaskEvaluator,
) -> EvaluationDecision:
    """Run task evaluation inside the private LangGraph state machine."""

    def evaluate_node(state: EvaluationState) -> dict[str, EvaluationDecision]:
        return {
            "decision": evaluator.evaluate(state["task"], repository, config),
        }

    builder = StateGraph(EvaluationState)
    builder.add_node("evaluate", evaluate_node)
    builder.add_edge(START, "evaluate")
    builder.add_edge("evaluate", END)
    final_state = builder.compile().invoke({"task": task})
    return cast(EvaluationDecision, final_state["decision"])
