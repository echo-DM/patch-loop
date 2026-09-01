from __future__ import annotations

import os

import pytest

from patchloop.adapters import EvaluationTask, ToolDefinition
from patchloop.config import DEFAULT_MODEL
from patchloop.gemini import GeminiPatchModelAdapter


@pytest.mark.smoke
def test_live_gemini_adapter_returns_a_controlled_turn() -> None:
    if os.environ.get("PATCHLOOP_RUN_GEMINI_SMOKE") != "1":
        pytest.skip("Set PATCHLOOP_RUN_GEMINI_SMOKE=1 to call the live Gemini API.")
    if not os.environ.get("PATCHLOOP_GEMINI_API_KEY", "").strip():
        pytest.skip("Live Gemini smoke requires PATCHLOOP_GEMINI_API_KEY.")
    adapter = GeminiPatchModelAdapter.from_environment(DEFAULT_MODEL)

    turn = adapter.next_turn(
        EvaluationTask(
            id="gemini-smoke",
            title="Inspect one file",
            body="Use list_files to inspect the repository root.",
        ),
        (),
        (ToolDefinition("list_files", "List files below a path.", (), ("path",)),),
    )

    assert turn.tool_calls or turn.completion
