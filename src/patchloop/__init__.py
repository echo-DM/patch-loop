"""PatchLoop's public execution interface."""

from patchloop.adapters import (
    DeterministicPatchModelAdapter,
    DeterministicModelAdapter,
    DeterministicVerifierAdapter,
    GitHubIssueAdapter,
    TaskEvaluator,
)
from patchloop.config import RepositoryConfig, load_repository_config
from patchloop.core import (
    GitHubEventTask,
    RunAdapters,
    RunRequest,
    RunResult,
    TaskSnapshot,
    run,
)

__all__ = [
    "GitHubEventTask",
    "GitHubIssueAdapter",
    "DeterministicPatchModelAdapter",
    "DeterministicModelAdapter",
    "DeterministicVerifierAdapter",
    "RepositoryConfig",
    "RunAdapters",
    "RunRequest",
    "RunResult",
    "TaskEvaluator",
    "TaskSnapshot",
    "load_repository_config",
    "run",
]
