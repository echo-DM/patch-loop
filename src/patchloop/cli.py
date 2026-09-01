from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, cast

from patchloop.adapters import ExplicitNoChangeEvaluator
from patchloop.config import load_repository_config
from patchloop.core import (
    RunAdapters,
    RunRequest,
    TaskSnapshot,
    run,
    validate_repository_workspace,
)
from patchloop.docker_verifier import DockerVerifierAdapter
from patchloop.errors import ConfigError, InfrastructureError, TaskError
from patchloop.gemini import GeminiPatchModelAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patchloop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--task", type=Path, required=True)
    run_parser.add_argument("--repository", type=Path, required=True)
    run_parser.add_argument(
        "--live",
        action="store_true",
        help="Use the live Gemini adapter and Docker verifier.",
    )
    return parser


def load_task(path: Path) -> TaskSnapshot:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise TaskError("invalid_task", "Task input is not a readable JSON document.") from error
    if not isinstance(loaded, dict):
        raise TaskError("invalid_task", "Task input must be a JSON object.")
    document = cast(dict[str, object], loaded)

    values: dict[str, str] = {}
    for field in ("id", "title", "body"):
        if field not in document:
            raise TaskError(
                "missing_task_field", f"Missing required task field: {field}."
            )
        value = document[field]
        if not isinstance(value, str) or not value.strip():
            raise TaskError(
                "invalid_task_field", f"Task field {field} must be a non-empty string."
            )
        values[field] = value
    return TaskSnapshot(
        id=values["id"],
        title=values["title"],
        body=values["body"],
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repository = cast(Path, arguments.repository)
    try:
        validate_repository_workspace(repository)
        task = load_task(cast(Path, arguments.task))
        config = load_repository_config(repository / ".patchloop.yml")
        adapters = (
            RunAdapters(
                model=GeminiPatchModelAdapter.from_environment(config.model),
                verifier=DockerVerifierAdapter(),
            )
            if cast(bool, arguments.live)
            else RunAdapters(evaluator=ExplicitNoChangeEvaluator())
        )
        result = run(
            RunRequest(
                task=task,
                repository=repository,
                config=config,
                adapters=adapters,
            )
        )
    except (TaskError, ConfigError, InfrastructureError) as error:
        if isinstance(error, TaskError):
            category = "task"
        elif isinstance(error, ConfigError):
            category = "configuration"
        else:
            category = "infrastructure"
        print(
            json.dumps(
                {
                    "error": {
                        "category": category,
                        "code": error.code,
                        "message": error.message,
                    }
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1 if isinstance(error, TaskError) else 2
    print(json.dumps(result.report, sort_keys=True))
    return 0 if result.terminal_outcome != "failed" else 1
