from __future__ import annotations

from pathlib import Path

from patchloop import RunAdapters, RunRequest, TaskSnapshot, load_repository_config, run
from patchloop.adapters import (
    CheckResult,
    DeterministicPatchModelAdapter,
    DeterministicVerifierAdapter,
    ModelTurn,
    ToolCall,
    VerificationResult,
    EvaluationDecision,
)


def test_controlled_tools_generate_an_integrity_checked_patch(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(tool_calls=(ToolCall("list", "list_files", {"path": "."}),)),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "search",
                        "search_code",
                        {"query": "Hello", "path": "."},
                    ),
                    ToolCall(
                        "read",
                        "read_file",
                        {"path": "README.md"},
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "Hello, PatchLoop!\n"},
                    ),
                    ToolCall("diff", "inspect_diff", {}),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete(
                summary="Update the fixture greeting.",
                actionable_message="Review the generated Draft PR.",
            ),
        )
    )
    verifier = DeterministicVerifierAdapter(
        VerificationResult.passed(("check greeting",))
    )

    result = run(
        RunRequest(
            task=TaskSnapshot(
                id="issue-104",
                title="Update greeting",
                body="Change the README greeting to Hello, PatchLoop!",
            ),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(model=model, verifier=verifier),
        )
    )

    assert result.terminal_outcome == "pr_created"
    assert (repository / "README.md").read_text() == "Hello, PatchLoop!\n"
    assert result.patch is not None
    assert result.patch.changed_files == ("README.md",)
    assert "-Hello, world!" in result.patch.content
    assert "+Hello, PatchLoop!" in result.patch.content
    assert result.patch.sha256 == result.report["patch"]["sha256"]
    assert result.report["integrity"] == {
        "algorithm": "sha256",
        "patch_sha256": result.patch.sha256,
    }
    assert result.patch.byte_length == len(result.patch.content.encode())
    assert result.verification["status"] == "checks_passed"
    assert result.report["changed_files"] == {"count": 1, "paths": ["README.md"]}
    assert result.report["budgets"]["usage"]["tool_calls"] == 6
    assert result.publication == {
        "intent": "draft_pr",
        "reason": "patch_generated",
        "patch_sha256": result.patch.sha256,
    }
    assert verifier.requests[0].repository == repository
    assert verifier.requests[0].checks == ("check greeting",)
    assert [tool.name for tool in model.available_tools] == [
        "list_files",
        "search_code",
        "read_file",
        "apply_patch",
        "inspect_diff",
        "run_checks",
    ]


def test_invalid_tool_requests_are_controlled_and_model_can_recover(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("do not change\n")
    symlink = repository / "linked.txt"
    symlink.symlink_to(outside)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "traversal",
                        "apply_patch",
                        {"path": "../outside.txt", "content": "escaped\n"},
                    ),
                    ToolCall(
                        "symlink",
                        "apply_patch",
                        {"path": "linked.txt", "content": "escaped\n"},
                    ),
                    ToolCall("shell", "shell", {"command": "echo unsafe"}),
                    ToolCall(
                        "credential",
                        "apply_patch",
                        {"path": "README.md", "content": "ghp_model_secret123\n"},
                    ),
                    ToolCall("bad-args", "read_file", {"path": "README.md", "x": 1}),
                    ToolCall("same", "read_file", {"path": "README.md"}),
                    ToolCall("same", "apply_patch", {"path": "README.md", "content": "bad"}),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "safe-edit",
                        "apply_patch",
                        {"path": "README.md", "content": "Hello, safely!\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Apply the safe edit.", "Review the Draft PR."),
        )
    )

    secret = "ghp_verifier_secret123"
    result = run(
        RunRequest(
            task=TaskSnapshot("issue-105", "Safe edit", "Update the greeting safely."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                model=model,
                verifier=DeterministicVerifierAdapter(
                    VerificationResult(
                        "checks_passed",
                        (CheckResult("check greeting", "passed", 0, secret),),
                    )
                ),
            ),
        )
    )

    assert result.terminal_outcome == "pr_created"
    assert outside.read_text() == "do not change\n"
    assert symlink.read_text() == "do not change\n"
    assert (repository / "README.md").read_text() == "Hello, safely!\n"
    rejected = model.observations[1]
    assert [observation.error["code"] for observation in rejected if observation.error] == [
        "unsafe_path",
        "unsafe_path",
        "unknown_tool",
        "credential_content_rejected",
        "invalid_arguments",
        "duplicate_tool_call",
    ]
    assert "ghp_model_secret123" not in result.patch.content
    assert secret not in str(result.report)


def test_patch_changed_after_checks_is_not_publishable(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "first-edit",
                        "apply_patch",
                        {"path": "README.md", "content": "Checked content.\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "unchecked-edit",
                        "apply_patch",
                        {"path": "README.md", "content": "Unchecked content.\n"},
                    ),
                )
            ),
            ModelTurn.complete("Finish the edit.", "Review the Draft PR."),
        )
    )

    result = run(
        RunRequest(
            task=TaskSnapshot("issue-106", "Safe verification", "Update the README."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                model=model,
                verifier=DeterministicVerifierAdapter(
                    VerificationResult.passed(("check greeting",))
                ),
            ),
        )
    )

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.report["changed_files"] == {"count": 1, "paths": ["README.md"]}
    assert result.report["errors"][0]["code"] == "patch_changed_after_verification"


def test_patch_model_can_stop_for_clarification_without_editing(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn.decide(
                EvaluationDecision(
                    terminal_outcome="needs_clarification",
                    summary="The target greeting is ambiguous.",
                    actionable_message="Choose the intended greeting.",
                    clarification_questions=("What exact greeting should replace the current one?",),
                )
            ),
        )
    )

    result = run(
        RunRequest(
            task=TaskSnapshot("issue-107", "Change greeting", "Make it better."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                model=model,
                verifier=DeterministicVerifierAdapter(
                    VerificationResult.passed(("check greeting",))
                ),
            ),
        )
    )

    assert result.terminal_outcome == "needs_clarification"
    assert result.patch is None
    assert result.publication["intent"] == "issue_feedback"
    assert (repository / "README.md").read_text() == "Hello, world!\n"


def test_replacing_a_hard_link_does_not_write_outside_workspace(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content\n")
    linked = repository / "linked.txt"
    linked.hardlink_to(outside)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit-link",
                        "apply_patch",
                        {"path": "linked.txt", "content": "workspace content\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Replace the linked file.", "Review the Draft PR."),
        )
    )

    result = run(
        RunRequest(
            task=TaskSnapshot("issue-108", "Replace file", "Replace linked.txt."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                model=model,
                verifier=DeterministicVerifierAdapter(
                    VerificationResult.passed(("check greeting",))
                ),
            ),
        )
    )

    assert result.terminal_outcome == "pr_created"
    assert linked.read_text() == "workspace content\n"
    assert outside.read_text() == "outside content\n"


def configured_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README.md").write_text("Hello, world!\n")
    (repository / ".patchloop.yml").write_text(
        """\
version: 1
model: deterministic
verifier:
  image: fixture
  setup: []
  checks:
    - check greeting
budgets:
  max_iterations: 3
  max_tool_calls: 60
  max_changed_files: 20
  max_diff_lines: 2000
  max_wall_time_minutes: 30
"""
    )
    return repository
