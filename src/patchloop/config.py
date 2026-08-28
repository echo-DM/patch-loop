from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from patchloop.errors import ConfigError, InfrastructureError


@dataclass(frozen=True)
class VerifierConfig:
    image: str
    setup: tuple[str, ...]
    checks: tuple[str, ...]


@dataclass(frozen=True)
class BudgetConfig:
    max_iterations: int
    max_tool_calls: int
    max_changed_files: int
    max_diff_lines: int
    max_wall_time_minutes: int


@dataclass(frozen=True)
class RepositoryConfig:
    version: int
    model: str
    verifier: VerifierConfig
    budgets: BudgetConfig


SAFETY_CEILINGS = {
    "max_iterations": 3,
    "max_tool_calls": 60,
    "max_changed_files": 20,
    "max_diff_lines": 2_000,
    "max_wall_time_minutes": 30,
}


def _required(document: dict[str, object], field: str, path: str | None = None) -> object:
    try:
        return document[field]
    except KeyError as error:
        field_path = path or field
        raise ConfigError(
            "missing_required_field",
            f"Missing required configuration field: {field_path}.",
        ) from error


def _mapping(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ConfigError(
            "invalid_config_value", f"{path} must be a mapping with string keys."
        )
    return cast(dict[str, object], value)


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("invalid_config_value", f"{path} must be a non-empty string.")
    return value


def _commands(value: object, path: str, *, require_one: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(command, str) and command.strip() for command in value
    ):
        raise ConfigError(
            "invalid_config_value", f"{path} must be a list of non-empty commands."
        )
    if require_one and not value:
        raise ConfigError(
            "invalid_config_value", f"{path} must contain at least one command."
        )
    return tuple(cast(list[str], value))


def _budget(document: dict[str, object], field: str) -> int:
    value = _required(document, field, f"budgets.{field}")
    ceiling = SAFETY_CEILINGS[field]
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        raise ConfigError(
            "unsafe_budget_value",
            f"budgets.{field} must be between 1 and {ceiling}; got {value}.",
        )
    return value


def load_repository_config(path: Path) -> RepositoryConfig:
    try:
        document = _mapping(yaml.safe_load(path.read_text()), "configuration")
    except FileNotFoundError as error:
        raise ConfigError(
            "config_not_found", f"Repository configuration not found: {path}."
        ) from error
    except yaml.YAMLError as error:
        raise ConfigError(
            "invalid_yaml", "Repository configuration is not valid YAML."
        ) from error
    except OSError as error:
        raise InfrastructureError(
            "config_unreadable", "Repository configuration could not be read."
        ) from error

    version = _required(document, "version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise ConfigError(
            "unsupported_config_version",
            f"Unsupported configuration version {version}; expected 1.",
        )

    verifier_document = _mapping(_required(document, "verifier"), "verifier")
    budgets_document = _mapping(_required(document, "budgets"), "budgets")
    return RepositoryConfig(
        version=version,
        model=_string(_required(document, "model"), "model"),
        verifier=VerifierConfig(
            image=_string(
                _required(verifier_document, "image", "verifier.image"),
                "verifier.image",
            ),
            setup=_commands(
                _required(verifier_document, "setup", "verifier.setup"),
                "verifier.setup",
                require_one=False,
            ),
            checks=_commands(
                _required(verifier_document, "checks", "verifier.checks"),
                "verifier.checks",
                require_one=True,
            ),
        ),
        budgets=BudgetConfig(
            max_iterations=_budget(budgets_document, "max_iterations"),
            max_tool_calls=_budget(budgets_document, "max_tool_calls"),
            max_changed_files=_budget(budgets_document, "max_changed_files"),
            max_diff_lines=_budget(budgets_document, "max_diff_lines"),
            max_wall_time_minutes=_budget(
                budgets_document, "max_wall_time_minutes"
            ),
        ),
    )
