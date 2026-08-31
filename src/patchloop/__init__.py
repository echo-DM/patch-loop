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
from patchloop.docker_verifier import DockerVerifierAdapter
from patchloop.gemini import GeminiPatchModelAdapter

__all__ = [
    "GitHubEventTask",
    "GitHubIssueAdapter",
    "GeminiPatchModelAdapter",
    "DeterministicPatchModelAdapter",
    "DeterministicModelAdapter",
    "DeterministicVerifierAdapter",
    "DockerVerifierAdapter",
    "RepositoryConfig",
    "RunAdapters",
    "RunRequest",
    "RunResult",
    "TaskEvaluator",
    "TaskSnapshot",
    "load_repository_config",
    "run",
]
