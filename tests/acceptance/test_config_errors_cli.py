from __future__ import annotations

import json

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


def test_missing_required_config_field_is_named(cli_harness: CliHarness) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(config.read_text().replace("model: gemini-2.5-flash\n", ""))

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "missing_required_field",
            "message": "Missing required configuration field: model.",
        }
    }


def test_budget_above_safety_ceiling_is_rejected(cli_harness: CliHarness) -> None:
    repository = cli_harness.copy_repository()
    config = repository / ".patchloop.yml"
    config.write_text(config.read_text().replace("max_tool_calls: 60", "max_tool_calls: 61"))

    completed = cli_harness.run(repository)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert json.loads(completed.stderr) == {
        "error": {
            "category": "configuration",
            "code": "unsafe_budget_value",
            "message": "budgets.max_tool_calls must be between 1 and 60; got 61.",
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
