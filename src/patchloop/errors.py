from __future__ import annotations


class PatchLoopError(Exception):
    """Base class for errors that cross the CLI boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ConfigError(PatchLoopError):
    """The repository configuration cannot authorize a run."""


class InfrastructureError(PatchLoopError):
    """The local execution environment cannot start a run."""


class TaskError(PatchLoopError):
    """The supplied task cannot enter the execution interface."""
