from __future__ import annotations

from pathlib import Path

import pytest

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
from patchloop.controlled_tools import ControlledTools


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


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_parts"),
    [
        (
            "read_file",
            {},
            ('Required fields: ["path"]', 'Missing required fields: ["path"]'),
        ),
        (
            "apply_patch",
            {"path": "README.md", "patch": "private-diff-value"},
            (
                'Missing required fields: ["content"]',
                'Unexpected fields: ["patch"]',
            ),
        ),
        (
            "search_code",
            {"query": "Hello", "regex": True},
            (
                'Required fields: ["query"]',
                'Optional fields: ["path"]',
                'Unexpected fields: ["regex"]',
            ),
        ),
        ("read_file", {"path": 7}, ("path must be a string",)),
        ("search_code", {"query": ""}, ("query must not be empty",)),
        (
            "inspect_diff",
            {"path": "README.md"},
            (
                "Required fields: []",
                "Optional fields: []",
                'Unexpected fields: ["path"]',
            ),
        ),
    ],
)
def test_argument_errors_identify_corrections_and_allow_a_valid_retry(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
    expected_parts: tuple[str, ...],
) -> None:
    repository = configured_repository(tmp_path)
    config = load_repository_config(repository / ".patchloop.yml")
    tools = ControlledTools(
        repository,
        config.verifier,
        config.budgets,
        DeterministicVerifierAdapter(VerificationResult.passed(("check greeting",))),
    )

    rejected = tools.execute(ToolCall("invalid", tool_name, arguments))

    assert not rejected.ok
    assert rejected.output is None
    assert rejected.error is not None
    assert rejected.error["code"] == "invalid_arguments"
    for part in expected_parts:
        assert part in rejected.error["message"]
    assert "private-diff-value" not in rejected.error["message"]
    assert (repository / "README.md").read_text() == "Hello, world!\n"
    corrected = tools.execute(
        ToolCall("corrected", "read_file", {"path": "README.md"})
    )
    assert corrected.ok
    assert corrected.output == "Hello, world!\n"


def test_argument_errors_redact_field_names_and_do_not_echo_values(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    config = load_repository_config(repository / ".patchloop.yml")
    tools = ControlledTools(
        repository,
        config.verifier,
        config.budgets,
        DeterministicVerifierAdapter(VerificationResult.passed(("check greeting",))),
    )
    field_secret = "ghp_argument_field_secret123"
    value_secret = "ghp_argument_value_secret123"

    result = tools.execute(
        ToolCall(
            "invalid",
            "read_file",
            {
                "path": value_secret,
                field_secret: "arbitrary-private-value",
            },
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error["code"] == "invalid_arguments"
    assert 'Required fields: ["path"]' in result.error["message"]
    assert "Unexpected fields" in result.error["message"]
    assert "[REDACTED]" in result.error["message"]
    assert field_secret not in result.error["message"]
    assert value_secret not in result.error["message"]
    assert "arbitrary-private-value" not in result.error["message"]


def test_long_argument_field_names_produce_bounded_actionable_errors(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    config = load_repository_config(repository / ".patchloop.yml")
    tools = ControlledTools(
        repository,
        config.verifier,
        config.budgets,
        DeterministicVerifierAdapter(VerificationResult.passed(("check greeting",))),
    )
    field_secret = "ghp_argument_field_secret123"

    result = tools.execute(
        ToolCall(
            "invalid",
            "read_file",
            {
                "field_" + "x" * 5_000: "private-value",
                field_secret: "another-private-value",
            },
        )
    )

    assert not result.ok
    assert result.error is not None
    message = result.error["message"]
    assert result.error["code"] == "invalid_arguments"
    assert len(message) <= 1_000
    assert message.endswith("[message truncated]")
    assert 'Missing required fields: ["path"]' in message
    assert "Supply missing required fields and remove unexpected fields" in message
    assert field_secret not in message
    assert "private-value" not in message


def test_passing_checks_terminalize_before_a_later_model_edit(tmp_path: Path) -> None:
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

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert "+Checked content." in result.patch.content
    assert (repository / "README.md").read_text() == "Checked content.\n"
    assert result.publication["intent"] == "draft_pr"
    assert result.report["changed_files"] == {"count": 1, "paths": ["README.md"]}
    assert result.report["errors"] == []


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


def test_patch_model_decision_after_edit_remains_invalid(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "Edited content.\n"},
                    ),
                )
            ),
            ModelTurn.decide(
                EvaluationDecision(
                    terminal_outcome="no_change",
                    summary="No change is needed.",
                    actionable_message="Close the issue.",
                )
            ),
        )
    )

    result = run(
        RunRequest(
            task=TaskSnapshot("issue-108", "Change greeting", "Make it better."),
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
    assert result.report["errors"][0]["code"] == "decision_after_edit"


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
  setup:
    - prepare fixture
  checks:
    - check greeting
  limits:
    timeout_seconds: 300
    memory_mb: 512
    pids: 64
    output_bytes: 65536
budgets:
  max_iterations: 3
  max_tool_calls: 60
  max_changed_files: 20
  max_diff_lines: 2000
  max_wall_time_minutes: 30
"""
    )
    return repository
