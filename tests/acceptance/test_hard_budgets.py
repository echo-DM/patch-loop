from __future__ import annotations

from pathlib import Path

from patchloop import RunAdapters, RunRequest, TaskSnapshot, load_repository_config, run
from patchloop.adapters import (
    CheckResult,
    DeterministicModelAdapter,
    DeterministicPatchModelAdapter,
    DeterministicVerifierAdapter,
    EvaluationDecision,
    ModelTurn,
    ToolCall,
    VerificationResult,
)
from patchloop.core import RunResult


def test_tool_call_limit_stops_a_batch_and_publishes_the_legal_patch(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path, max_tool_calls=2)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "bounded change\n"},
                    ),
                    ToolCall("inspect", "inspect_diff", {}),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
        )
    )

    result = run_patch(repository, model)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.patch.changed_files == ("README.md",)
    assert result.verification["status"] == "budget_exhausted"
    assert result.publication == {
        "intent": "draft_pr",
        "reason": "patch_generated",
        "patch_sha256": result.patch.sha256,
    }
    assert result.report["budgets"]["usage"]["tool_calls"] == 2
    assert result.report["budgets"]["resource_limit_events"] == [
        {
            "category": "budget",
            "code": "tool_call_limit_reached",
            "message": "The configured controlled-tool call limit was reached.",
        }
    ]


def test_changed_file_limit_rolls_back_only_the_excess_file(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, max_changed_files=1)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "first",
                        "apply_patch",
                        {"path": "README.md", "content": "legal change\n"},
                    ),
                    ToolCall(
                        "excess",
                        "apply_patch",
                        {"path": "SECOND.md", "content": "must be rolled back\n"},
                    ),
                )
            ),
        )
    )

    result = run_patch(repository, model)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.patch.changed_files == ("README.md",)
    assert not (repository / "SECOND.md").exists()
    assert result.verification["status"] == "budget_exhausted"
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "changed_file_limit_reached"
    )


def test_diff_line_limit_rolls_back_the_oversized_replacement(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, max_diff_lines=2)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "first",
                        "apply_patch",
                        {"path": "README.md", "content": "legal change\n"},
                    ),
                    ToolCall(
                        "excess",
                        "apply_patch",
                        {"path": "README.md", "content": "too\nmany\nlines\n"},
                    ),
                )
            ),
        )
    )

    result = run_patch(repository, model)

    assert result.patch is not None
    assert result.patch.changed_files == ("README.md",)
    assert (repository / "README.md").read_text() == "legal change\n"
    assert result.report["budgets"]["usage"]["diff_lines"] == 2
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "diff_line_limit_reached"
    )


def test_oversized_file_is_rejected_before_it_enters_the_patch(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "first",
                        "apply_patch",
                        {"path": "README.md", "content": "legal change\n"},
                    ),
                    ToolCall(
                        "oversized",
                        "apply_patch",
                        {"path": "large.txt", "content": "x" * 1_000_001},
                    ),
                )
            ),
        )
    )

    result = run_patch(repository, model)

    assert result.patch is not None
    assert result.patch.changed_files == ("README.md",)
    assert not (repository / "large.txt").exists()
    assert result.report["budgets"]["limits"]["max_file_bytes"] == 1_000_000
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "patched_file_size_limit_reached"
    )


def test_protected_authority_paths_are_rejected_and_recorded(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    workflow = repository / ".github" / "workflows" / "patchloop.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("permissions: {}\n")
    git_config = repository / ".git" / "config"
    git_config.parent.mkdir()
    git_config.write_text("trusted\n")
    original_config = (repository / ".patchloop.yml").read_text()
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "workflow",
                        "apply_patch",
                        {"path": ".github/workflows/patchloop.yml", "content": "permissions: write-all\n"},
                    ),
                    ToolCall(
                        "policy",
                        "apply_patch",
                        {"path": ".patchloop.yml", "content": "unsafe\n"},
                    ),
                    ToolCall(
                        "git",
                        "apply_patch",
                        {"path": ".git/config", "content": "unsafe\n"},
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "safe",
                        "apply_patch",
                        {"path": "README.md", "content": "safe change\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Apply the safe change.", "Review the Draft PR."),
        )
    )

    result = run_patch(repository, model)

    assert result.terminal_outcome == "pr_created"
    assert result.verification["status"] == "checks_passed"
    assert workflow.read_text() == "permissions: {}\n"
    assert git_config.read_text() == "trusted\n"
    assert (repository / ".patchloop.yml").read_text() == original_config
    assert [
        event["code"] for event in result.report["budgets"]["resource_limit_events"]
    ] == ["protected_path", "protected_path", "protected_path"]
    assert [result.error["code"] for result in model.observations[1]] == [
        "protected_path",
        "protected_path",
        "protected_path",
    ]


def test_passing_final_iteration_stops_an_additional_edit_cycle(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, max_iterations=1)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "first-edit",
                        "apply_patch",
                        {"path": "README.md", "content": "first iteration\n"},
                    ),
                    ToolCall("first-check", "run_checks", {}),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "second-edit",
                        "apply_patch",
                        {"path": "SECOND.md", "content": "second iteration\n"},
                    ),
                    ToolCall("second-check", "run_checks", {}),
                )
            ),
        )
    )

    result = run_patch(repository, model)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.patch.changed_files == ("README.md",)
    assert not (repository / "SECOND.md").exists()
    assert result.verification["status"] == "checks_passed"
    assert result.report["budgets"]["usage"]["iterations"] == 1
    assert result.report["budgets"]["resource_limit_events"] == []


def test_failed_final_iteration_immediately_exhausts_the_budget(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, max_iterations=1)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "still failing\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Must not complete.", "Must not hide exhaustion."),
        )
    )
    failed = VerificationResult(
        "checks_failed",
        (CheckResult("check fixture", "failed", 1, "fixture failure"),),
    )

    result = run_patch(repository, model, verification=failed)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.verification["status"] == "budget_exhausted"
    assert result.report["budgets"]["usage"]["iterations"] == 1
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "iteration_limit_reached"
    )


class StepClock:
    def __init__(self, values: tuple[float, ...]) -> None:
        self._values = iter(values)
        self._last = values[-1]

    def __call__(self) -> float:
        return next(self._values, self._last)


def test_wall_time_limit_is_checked_after_a_tool_and_preserves_its_patch(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path, max_wall_time_minutes=1)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "timed change\n"},
                    ),
                    ToolCall("must-not-run", "run_checks", {}),
                )
            ),
        )
    )
    clock = StepClock((0, 0, 0, 0, 61))

    result = run_patch(repository, model, clock=clock)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.verification["status"] == "budget_exhausted"
    assert result.report["budgets"]["usage"]["tool_calls"] == 1
    assert result.report["budgets"]["usage"]["wall_time_minutes"] == 1
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "wall_time_limit_reached"
    )


def test_wall_time_limit_is_checked_after_final_patch_construction(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path, max_wall_time_minutes=1)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "last-second change\n"},
                    ),
                )
            ),
            ModelTurn.complete("Finish in time.", "Review the Draft PR."),
        )
    )
    clock = StepClock((0, 0, 0, 0, 0, 0, 0, 61))

    result = run_patch(repository, model, clock=clock)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.verification["status"] == "budget_exhausted"
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "wall_time_limit_reached"
    )


def test_budget_exhaustion_without_a_legal_patch_does_not_publish(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path, max_tool_calls=1)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall("read", "read_file", {"path": "README.md"}),
                    ToolCall("read-again", "read_file", {"path": "README.md"}),
                )
            ),
        )
    )

    result = run_patch(repository, model)

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.verification["status"] == "budget_exhausted"


def test_total_runtime_includes_task_evaluation_before_the_patch_loop(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path, max_wall_time_minutes=1)
    evaluator = DeterministicModelAdapter(
        {
            "issue-105": EvaluationDecision(
                terminal_outcome="no_change",
                summary="No change would otherwise be needed.",
                actionable_message="Do not publish.",
            )
        }
    )

    result = run(
        RunRequest(
            task=TaskSnapshot("issue-105", "Slow evaluation", "Evaluate this task."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(evaluator=evaluator, clock=StepClock((0, 61))),
        )
    )

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.verification["status"] == "budget_exhausted"
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.report["budgets"]["usage"]["wall_time_minutes"] == 1
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "wall_time_limit_reached"
    )


def run_patch(
    repository: Path,
    model: DeterministicPatchModelAdapter,
    *,
    clock: StepClock | None = None,
    verification: VerificationResult | None = None,
) -> RunResult:
    return run(
        RunRequest(
            task=TaskSnapshot("issue-105", "Bound the patch", "Make a bounded change."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                model=model,
                verifier=DeterministicVerifierAdapter(
                    verification or VerificationResult.passed(("check fixture",))
                ),
                **({"clock": clock} if clock is not None else {}),
            ),
        )
    )


def configured_repository(
    tmp_path: Path,
    *,
    max_iterations: int = 3,
    max_tool_calls: int = 60,
    max_changed_files: int = 20,
    max_diff_lines: int = 2000,
    max_wall_time_minutes: int = 30,
) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README.md").write_text("original\n")
    (repository / ".patchloop.yml").write_text(
        f"""\
version: 1
model: deterministic
verifier:
  image: fixture
  setup:
    - prepare fixture
  checks:
    - check fixture
  limits:
    timeout_seconds: 300
    memory_mb: 512
    pids: 64
    output_bytes: 65536
budgets:
  max_iterations: {max_iterations}
  max_tool_calls: {max_tool_calls}
  max_changed_files: {max_changed_files}
  max_diff_lines: {max_diff_lines}
  max_wall_time_minutes: {max_wall_time_minutes}
"""
    )
    return repository
