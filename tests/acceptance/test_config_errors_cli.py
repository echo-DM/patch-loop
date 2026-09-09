from __future__ import annotations

import json

import pytest

from conftest import CliHarness


def test_unknown_config_version_is_a_configuration_error(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(config.read_text().replace("version: 1", "version: 2"))

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "unsupported_config_version",
            "message": "Unsupported configuration version 2; expected 1.",
        }
    }


def test_missing_model_uses_the_default(cli_harness: CliHarness) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace("model: gemini-3.5-flash-lite\n", "")
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["model"] == {
        "provider": "configured",
        "name": "gemini-3.5-flash-lite",
    }


def test_model_identifier_is_validated_before_execution(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            "model: gemini-3.5-flash-lite", 'model: "bad model\\nname"'
        )
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["error"] == {
        "category": "configuration",
        "code": "invalid_config_value",
        "message": "model must be a valid provider model identifier.",
    }


def test_verifier_requires_at_least_one_setup_command(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            "  setup:\n    - uv sync --frozen\n",
            "  setup: []\n",
        )
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "invalid_config_value",
            "message": "verifier.setup must contain at least one command.",
        }
    }


def test_verifier_resource_limits_are_required(cli_harness: CliHarness) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            "  limits:\n"
            "    timeout_seconds: 300\n"
            "    memory_mb: 512\n"
            "    pids: 64\n"
            "    output_bytes: 65536\n",
            "",
        )
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "missing_required_field",
            "message": "Missing required configuration field: verifier.limits.",
        }
    }


def test_verifier_commands_must_have_a_bounded_single_line(cli_harness: CliHarness) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            "    - uv run pytest\n",
            '    - "uv run pytest\\nprintenv"\n',
        )
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "invalid_config_value",
            "message": (
                "verifier.checks commands must be single-line strings of at most "
                "4096 characters."
            ),
        }
    }


def test_verifier_image_cannot_be_a_docker_option(cli_harness: CliHarness) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            "image: python:3.12-slim",
            "image: --privileged",
        )
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "invalid_config_value",
            "message": "verifier.image must be a valid Docker image reference.",
        }
    }


@pytest.mark.parametrize(
    ("field", "configured", "minimum", "ceiling"),
    (
        ("timeout_seconds", 300, 1, 1800),
        ("memory_mb", 512, 6, 4096),
        ("pids", 64, 1, 512),
        ("output_bytes", 65536, 1, 1000000),
    ),
)
def test_verifier_resource_limit_above_safety_ceiling_is_rejected(
    cli_harness: CliHarness,
    field: str,
    configured: int,
    minimum: int,
    ceiling: int,
) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(
            f"{field}: {configured}",
            f"{field}: {ceiling + 1}",
        )
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "unsafe_verifier_limit",
            "message": (
                f"verifier.limits.{field} must be between {minimum} and {ceiling}; "
                f"got {ceiling + 1}."
            ),
        }
    }


def test_verifier_memory_limit_below_docker_minimum_is_rejected(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(config.read_text().replace("memory_mb: 512", "memory_mb: 1"))

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert json.loads(completed.stderr)["error"] == {
        "category": "configuration",
        "code": "unsafe_verifier_limit",
        "message": "verifier.limits.memory_mb must be between 6 and 4096; got 1.",
    }


@pytest.mark.parametrize(
    ("field", "ceiling"),
    (
        ("max_iterations", 3),
        ("max_tool_calls", 60),
        ("max_changed_files", 20),
        ("max_diff_lines", 2000),
        ("max_wall_time_minutes", 30),
    ),
)
def test_budget_above_safety_ceiling_is_rejected(
    cli_harness: CliHarness, field: str, ceiling: int
) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(
        config.read_text().replace(f"{field}: {ceiling}", f"{field}: {ceiling + 1}")
    )

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "unsafe_budget_value",
            "message": (
                f"budgets.{field} must be between 1 and {ceiling}; got {ceiling + 1}."
            ),
        }
    }


def test_invalid_yaml_error_does_not_echo_configuration(
    cli_harness: CliHarness,
) -> None:
    repository = cli_harness.copy_repository()
    secret = "ghp_configuration_secret"
    (repository / ".patchloop.yml").write_text(f"version: 1\nmodel: [{secret}\n")

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "invalid_yaml",
            "message": "Repository configuration is not valid YAML.",
        }
    }
    assert secret not in completed.stderr
