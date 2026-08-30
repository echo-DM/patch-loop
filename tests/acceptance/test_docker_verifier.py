from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from patchloop import (
    DockerVerifierAdapter,
    RunAdapters,
    RunRequest,
    TaskSnapshot,
    load_repository_config,
    run,
)
from patchloop.adapters import DeterministicPatchModelAdapter, ModelTurn, ToolCall


DOCKER_IMAGE = os.environ.get("PATCHLOOP_TEST_DOCKER_IMAGE", "alpine:3.22")
DEFAULT_CHECK = (
    'test "$(cat README.md)" = verified'
    ' && test -z "$GEMINI_API_KEY"'
    ' && test -z "$GITHUB_TOKEN"'
    " && test ! -e .env"
    " && test ! -e .patchloop"
    " && test ! -e .patchloop-state.json"
    " && printf check > check-side-effect.txt"
)


def docker_image_available() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", DOCKER_IMAGE],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


@pytest.mark.integration
@pytest.mark.skipif(not docker_image_available(), reason="Docker fixture image unavailable")
def test_real_docker_verifier_isolates_secrets_and_discards_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = configured_repository(tmp_path)
    secret = "ghp_verifier_process_secret123"
    model_secret = "AIzaSyVerifierModelSecret123456789"
    (repository / ".env").write_text(
        f"GITHUB_TOKEN={secret}\nGEMINI_API_KEY={model_secret}\n"
    )
    (repository / ".patchloop").mkdir()
    (repository / ".patchloop" / "private-state.json").write_text("private\n")
    (repository / ".patchloop-state.json").write_text("private\n")
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    monkeypatch.setenv("GEMINI_API_KEY", model_secret)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "verified\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Verify the patch.", "Review the Draft PR."),
        )
    )

    result = run(
        RunRequest(
            task=TaskSnapshot("issue-600", "Verify patch", "Update the README."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(model=model, verifier=DockerVerifierAdapter()),
        )
    )

    assert result.terminal_outcome == "pr_created"
    assert result.verification["status"] == "checks_passed"
    assert (repository / "README.md").read_text() == "verified\n"
    assert (repository / ".env").exists()
    assert (repository / ".patchloop" / "private-state.json").exists()
    assert (repository / ".patchloop-state.json").exists()
    assert not (repository / "setup-side-effect.txt").exists()
    assert not (repository / "check-side-effect.txt").exists()
    assert secret not in str(result.report)
    assert model_secret not in str(result.report)


@pytest.mark.integration
@pytest.mark.skipif(not docker_image_available(), reason="Docker fixture image unavailable")
def test_candidate_patch_cannot_reintroduce_a_repository_env_file(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "env-edit",
                        "apply_patch",
                        {"path": ".env", "content": "DEBUG=true\n"},
                    ),
                )
            ),
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "safe-edit",
                        "apply_patch",
                        {"path": "README.md", "content": "verified\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Verify the patch.", "Review the Draft PR."),
        )
    )

    result = run(
        RunRequest(
            task=TaskSnapshot("issue-602", "Verify patch", "Update the README."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(model=model, verifier=DockerVerifierAdapter()),
        )
    )

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.patch.changed_files == ("README.md",)
    assert model.observations[1][0].error["code"] == "protected_path"
    assert not (repository / ".env").exists()


@pytest.mark.integration
@pytest.mark.skipif(not docker_image_available(), reason="Docker fixture image unavailable")
def test_setup_failure_is_distinct_and_prevents_patch_publication(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            "test ! -e .env && printf setup > setup-side-effect.txt",
            "printf setup-failed && exit 7",
        )
    )

    result = run_patch(repository)

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.verification["status"] == "setup_failed"
    assert result.verification["setup"] == [
        {
            "command": "setup-1",
            "status": "failed",
            "exit_code": 7,
            "output": "setup-failed",
            "failure_category": "command_failed",
            "output_truncated": False,
        }
    ]
    assert result.verification["checks"] == []
    assert result.report["errors"][0]["code"] == "verifier_setup_failed"


@pytest.mark.integration
@pytest.mark.skipif(not docker_image_available(), reason="Docker fixture image unavailable")
def test_check_failure_keeps_patch_and_reports_redacted_bounded_evidence(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    config = repository / ".patchloop.yml"
    secret = "ghp_check_output_secret123"
    config.write_text(
        config.read_text().replace(
            DEFAULT_CHECK,
            f"printf {secret} && exit 3",
        )
    )

    result = run_patch(repository)

    assert result.terminal_outcome == "pr_created"
    assert result.patch is not None
    assert result.verification["status"] == "checks_failed"
    assert result.verification["checks"] == [
        {
            "command": "check-1",
            "status": "failed",
            "exit_code": 3,
            "output": "[REDACTED]",
            "failure_category": "command_failed",
            "output_truncated": False,
        }
    ]
    assert result.publication["intent"] == "draft_pr"
    assert secret not in str(result.report)


def test_docker_infrastructure_failure_is_distinct(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)

    result = run_patch(
        repository,
        verifier=DockerVerifierAdapter(executable="patchloop-missing-docker"),
    )

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.verification["status"] == "infrastructure_failed"
    assert result.verification["setup"][0]["failure_category"] == "infrastructure"
    assert result.report["errors"] == [
        {
            "category": "infrastructure",
            "code": "verifier_infrastructure_failed",
            "message": "Docker could not complete the verifier run.",
        }
    ]
    assert result.publication == {"intent": "none", "reason": "failed"}


@pytest.mark.integration
@pytest.mark.skipif(not docker_image_available(), reason="Docker fixture image unavailable")
def test_check_timeout_is_normalized_and_stops_the_container(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text()
        .replace(
            DEFAULT_CHECK,
            "sleep 5",
        )
        .replace("timeout_seconds: 30", "timeout_seconds: 1")
    )

    result = run_patch(repository)

    assert result.terminal_outcome == "pr_created"
    assert result.verification["status"] == "checks_failed"
    check = result.verification["checks"][0]
    assert check["command"] == "check-1"
    assert check["exit_code"] == 124
    assert check["failure_category"] == "timeout"
    assert check["output_truncated"] is False
    assert result.report["budgets"]["resource_limit_events"] == [
        {
            "category": "resource",
            "code": "verifier_timeout",
            "message": "Verifier command check-1 reached its timeout limit.",
        }
    ]


@pytest.mark.integration
@pytest.mark.skipif(not docker_image_available(), reason="Docker fixture image unavailable")
def test_check_output_limit_is_truncated_and_normalized(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text()
        .replace(
            DEFAULT_CHECK,
            "yes ghp_output_limit_secret123",
        )
        .replace("output_bytes: 4096", "output_bytes: 64")
    )

    result = run_patch(repository)

    assert result.terminal_outcome == "pr_created"
    assert result.verification["status"] == "checks_failed"
    check = result.verification["checks"][0]
    assert check["command"] == "check-1"
    assert check["failure_category"] == "output_limit"
    assert check["output_truncated"] is True
    assert check["output"].endswith("[output truncated]")
    assert "ghp_" not in check["output"]
    assert len(check["output"].encode()) <= 84


@pytest.mark.integration
@pytest.mark.skipif(not docker_image_available(), reason="Docker fixture image unavailable")
def test_docker_applies_memory_and_process_limits(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            DEFAULT_CHECK,
            'test "$(cat /sys/fs/cgroup/memory.max)" = 134217728 && test "$(cat /sys/fs/cgroup/pids.max)" = 32',
        )
    )

    result = run_patch(repository)

    assert result.terminal_outcome == "pr_created"
    assert result.verification["status"] == "checks_passed"
    assert result.verification["checks"][0]["exit_code"] == 0


def run_patch(
    repository: Path,
    *,
    verifier: DockerVerifierAdapter | None = None,
):
    model = DeterministicPatchModelAdapter(
        (
            ModelTurn(
                tool_calls=(
                    ToolCall(
                        "edit",
                        "apply_patch",
                        {"path": "README.md", "content": "verified\n"},
                    ),
                    ToolCall("checks", "run_checks", {}),
                )
            ),
            ModelTurn.complete("Verify the patch.", "Review the Draft PR."),
        )
    )
    return run(
        RunRequest(
            task=TaskSnapshot("issue-601", "Verify patch", "Update the README."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(model=model, verifier=verifier or DockerVerifierAdapter()),
        )
    )


def configured_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README.md").write_text("original\n")
    (repository / ".patchloop.yml").write_text(
        f"""\
version: 1
model: deterministic
verifier:
  image: {DOCKER_IMAGE}
  setup:
    - test ! -e .env && printf setup > setup-side-effect.txt
  checks:
    - {DEFAULT_CHECK}
  limits:
    timeout_seconds: 30
    memory_mb: 128
    pids: 32
    output_bytes: 4096
budgets:
  max_iterations: 3
  max_tool_calls: 60
  max_changed_files: 20
  max_diff_lines: 2000
  max_wall_time_minutes: 30
"""
    )
    return repository
