from __future__ import annotations

import hashlib
from pathlib import Path

from patchloop import RunAdapters, RunRequest, TaskSnapshot, load_repository_config, run
from patchloop.adapters import (
    CheckResult,
    DeterministicPatchModelAdapter,
    ModelTurn,
    ToolCall,
    VerificationRequest,
    VerificationResult,
)


class SequenceVerifier:
    def __init__(self, results: tuple[VerificationResult, ...]) -> None:
        self._results = iter(results)
        self.requests: list[VerificationRequest] = []

    def verify(self, request: VerificationRequest) -> VerificationResult:
        self.requests.append(request)
        return next(self._results)


def test_failed_check_feedback_can_drive_a_successful_repair(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, max_iterations=3, output_bytes=80)
    secret = "ghp_repair_feedback_secret123"
    verifier = SequenceVerifier(
        (
            VerificationResult(
                "checks_failed",
                (
                    CheckResult(
                        "check greeting",
                        "failed",
                        1,
                        f"expected repaired; token={secret}; " + "x" * 200,
                        "command_failed",
                    ),
                ),
            ),
            VerificationResult.passed(("check greeting",)),
        )
    )
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "first-edit",
                        "apply_patch",
                        {"path": "README.md", "content": "broken\n"},
                    ),
                    ToolCall("first-check", "run_checks", {}),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "repair",
                        "apply_patch",
                        {"path": "README.md", "content": "repaired\n"},
                    ),
                    ToolCall("second-check", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Repair the greeting.", "Review the Draft PR."),
        )
    )

    result = run_patch(repository, model, verifier)

    assert result.terminal_outcome == "pr_created"
    assert result.verification["status"] == "checks_passed"
    assert result.report["budgets"]["usage"]["iterations"] == 2
    assert result.report["budgets"]["usage"]["tool_calls"] == 4
    assert result.report["budgets"]["usage"]["changed_files"] == 1
    assert (repository / "README.md").read_text() == "repaired\n"
    assert result.patch is not None
    assert "+repaired" in result.patch.content
    assert "+broken" not in result.patch.content
    first_feedback = model.observations[1][1].output
    assert isinstance(first_feedback, dict)
    first_output = first_feedback["checks"][0]["output"]
    assert secret not in first_output
    assert len(first_output.encode()) <= 80
    assert first_feedback["checks"][0]["output_truncated"] is True
    attempts = result.verification["attempts"]
    assert [attempt["status"] for attempt in attempts] == [
        "checks_failed",
        "checks_passed",
    ]
    assert attempts[0]["patch_sha256"] == hashlib.sha256(
        verifier.requests[0].patch.encode()
    ).hexdigest()
    assert attempts[1]["patch_sha256"] == result.patch.sha256
    assert secret not in str(result.report)
    assert verifier.requests[0].original_files == verifier.requests[1].original_files


def test_repeated_check_failures_preserve_the_last_patch_and_failure(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path, max_iterations=2)
    verifier = SequenceVerifier(
        (
            failed_verification("first failure"),
            failed_verification("last failure"),
        )
    )
    model = DeterministicPatchModelAdapter(
        (
            edit_and_check("first", "first attempt\n"),
            edit_and_check("second", "useful partial repair\n"),
        )
    )

    result = run_patch(repository, model, verifier)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert "+useful partial repair" in result.patch.content
    assert result.verification["status"] == "budget_exhausted"
    assert result.verification["checks"][0]["output"] == "last failure"
    assert result.report["budgets"]["usage"]["iterations"] == 2
    assert result.report["budgets"]["resource_limit_events"][-1]["code"] == (
        "iteration_limit_reached"
    )
    assert [
        attempt["checks"][0]["output"]
        for attempt in result.verification["attempts"]
    ] == ["first failure", "last failure"]
    assert result.publication["intent"] == "draft_pr"


def test_verifier_infrastructure_failure_interrupts_repair_without_publication(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path, max_iterations=3)
    verifier = SequenceVerifier(
        (
            failed_verification("ordinary failure"),
            VerificationResult(
                "infrastructure_failed",
                (
                    CheckResult(
                        "check greeting",
                        "failed",
                        125,
                        "Docker daemon unavailable",
                        "infrastructure",
                    ),
                ),
            ),
        )
    )
    model = DeterministicPatchModelAdapter(
        (
            edit_and_check("first", "first attempt\n"),
            edit_and_check("second", "second attempt\n"),
        )
    )

    result = run_patch(repository, model, verifier)

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.verification["status"] == "infrastructure_failed"
    assert result.report["errors"] == [
        {
            "category": "infrastructure",
            "code": "verifier_infrastructure_failed",
            "message": "Docker could not complete the verifier run.",
        }
    ]
    assert len(model.observations) == 2
    assert result.publication == {"intent": "none", "reason": "failed"}


def test_model_failure_without_a_legal_patch_remains_distinct(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path, max_iterations=3)
    verifier = SequenceVerifier((failed_verification("must not run"),))
    model = DeterministicPatchModelAdapter(
        (ModelTurn.complete("Nothing was edited.", "Retry the model."),)
    )

    result = run_patch(repository, model, verifier)

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.verification["status"] == "not_run"
    assert result.report["errors"][0]["category"] == "model"
    assert result.report["errors"][0]["code"] == "empty_patch"
    assert verifier.requests == []


def edit_and_check(call_prefix: str, content: str) -> ModelTurn:
    return ModelTurn(
        tool_calls=(
            ToolCall(
                f"{call_prefix}-edit",
                "apply_patch",
                {"path": "README.md", "content": content},
            ),
            ToolCall(f"{call_prefix}-check", "run_checks", {}),
        )
    )


def failed_verification(output: str) -> VerificationResult:
    return VerificationResult(
        "checks_failed",
        (
            CheckResult(
                "check greeting", "failed", 1, output, "command_failed"
            ),
        ),
    )


def run_patch(
    repository: Path,
    model: DeterministicPatchModelAdapter,
    verifier: SequenceVerifier,
):
    return run(
        RunRequest(
            task=TaskSnapshot(
                "issue-700", "Repair greeting", "Make the greeting pass checks."
            ),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(model=model, verifier=verifier),
        )
    )


def configured_repository(
    tmp_path: Path, *, max_iterations: int, output_bytes: int = 65_536
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
    - check greeting
  limits:
    timeout_seconds: 300
    memory_mb: 512
    pids: 64
    output_bytes: {output_bytes}
budgets:
  max_iterations: {max_iterations}
  max_tool_calls: 60
  max_changed_files: 20
  max_diff_lines: 2000
  max_wall_time_minutes: 30
"""
    )
    return repository
