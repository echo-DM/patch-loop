from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from patchloop.errors import ConfigError, InfrastructureError


@dataclass(frozen=True)
class VerifierLimits:
    timeout_seconds: int
    memory_mb: int
    pids: int
    output_bytes: int


@dataclass(frozen=True)
class VerifierConfig:
    image: str
    setup: tuple[str, ...]
    checks: tuple[str, ...]
    limits: VerifierLimits


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

VERIFIER_LIMIT_CEILINGS = {
    "timeout_seconds": 1_800,
    "memory_mb": 4_096,
    "pids": 512,
    "output_bytes": 1_000_000,
}

VERIFIER_LIMIT_MINIMUMS = {
    "timeout_seconds": 1,
    "memory_mb": 6,
    "pids": 1,
    "output_bytes": 1,
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


def _docker_image(value: object) -> str:
    image = _string(value, "verifier.image")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}", image) is None:
        raise ConfigError(
            "invalid_config_value",
            "verifier.image must be a valid Docker image reference.",
        )
    return image


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
    commands = cast(list[str], value)
    if any(
        "\n" in command
        or "\r" in command
        or "\x00" in command
        or len(command) > 4_096
        for command in commands
    ):
        raise ConfigError(
            "invalid_config_value",
            f"{path} commands must be single-line strings of at most 4096 characters.",
        )
    return tuple(commands)


def _budget(document: dict[str, object], field: str) -> int:
    value = _required(document, field, f"budgets.{field}")
    ceiling = SAFETY_CEILINGS[field]
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
        received = (
            f"got {value}"
            if isinstance(value, int) and not isinstance(value, bool)
            else "got an invalid value"
        )
        raise ConfigError(
            "unsafe_budget_value",
            f"budgets.{field} must be between 1 and {ceiling}; {received}.",
        )
    return value


def _verifier_limit(document: dict[str, object], field: str) -> int:
    value = _required(document, field, f"verifier.limits.{field}")
    minimum = VERIFIER_LIMIT_MINIMUMS[field]
    ceiling = VERIFIER_LIMIT_CEILINGS[field]
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= ceiling
    ):
        received = (
            f"got {value}"
            if isinstance(value, int) and not isinstance(value, bool)
            else "got an invalid value"
        )
        raise ConfigError(
            "unsafe_verifier_limit",
            f"verifier.limits.{field} must be between {minimum} and {ceiling}; "
            f"{received}.",
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
        received = (
            f" {version}"
            if isinstance(version, int) and not isinstance(version, bool)
            else ""
        )
        raise ConfigError(
            "unsupported_config_version",
            f"Unsupported configuration version{received}; expected 1.",
        )

    verifier_document = _mapping(_required(document, "verifier"), "verifier")
    verifier_limits_document = _mapping(
        _required(verifier_document, "limits", "verifier.limits"),
        "verifier.limits",
    )
    budgets_document = _mapping(_required(document, "budgets"), "budgets")
    return RepositoryConfig(
        version=version,
        model=_string(_required(document, "model"), "model"),
        verifier=VerifierConfig(
            image=_docker_image(
                _required(verifier_document, "image", "verifier.image"),
            ),
            setup=_commands(
                _required(verifier_document, "setup", "verifier.setup"),
                "verifier.setup",
                require_one=True,
            ),
            checks=_commands(
                _required(verifier_document, "checks", "verifier.checks"),
                "verifier.checks",
                require_one=True,
            ),
            limits=VerifierLimits(
                timeout_seconds=_verifier_limit(
                    verifier_limits_document, "timeout_seconds"
                ),
                memory_mb=_verifier_limit(verifier_limits_document, "memory_mb"),
                pids=_verifier_limit(verifier_limits_document, "pids"),
                output_bytes=_verifier_limit(
                    verifier_limits_document, "output_bytes"
                ),
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
