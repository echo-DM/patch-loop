from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, cast

from patchloop.config import ConfigError, load_repository_config
from patchloop.core import RunRequest, TaskSnapshot, run


class TaskError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="patchloop")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--task", type=Path, required=True)
    run_parser.add_argument("--repository", type=Path, required=True)
    return parser


def load_task(path: Path) -> TaskSnapshot:
    try:
        loaded = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as error:
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
        result = run(
            RunRequest(
                task=load_task(cast(Path, arguments.task)),
                repository=repository,
                config=load_repository_config(repository / ".patchloop.yml"),
            )
        )
    except (TaskError, ConfigError) as error:
        category = "task" if isinstance(error, TaskError) else "configuration"
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
    return 0
