from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).parents[1]
DOCKER_IMAGE = os.environ.get("PATCHLOOP_TEST_DOCKER_IMAGE", "alpine:3.22")


def run_step(label: str, command: list[str]) -> None:
    print(f"==> {label}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    try:
        run_step("Docker daemon", ["docker", "info"])
        run_step("Docker fixture image", ["docker", "image", "inspect", DOCKER_IMAGE])
        run_step("Locked dependencies", ["uv", "lock", "--check"])
        run_step("Strict type checking", ["uv", "run", "mypy"])
        run_step("Offline acceptance suite", ["uv", "run", "pytest"])
        with tempfile.TemporaryDirectory(prefix="patchloop-dist-") as output:
            run_step("Wheel and source distribution", ["uv", "build", "--out-dir", output])
        run_step("Whitespace validation", ["git", "diff", "--check"])
    except FileNotFoundError as error:
        print(f"Required command is unavailable: {error.filename}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(
            f"Offline validation failed with exit code {error.returncode}: "
            f"{' '.join(error.cmd)}",
            file=sys.stderr,
        )
        return error.returncode or 1
    print("PatchLoop offline validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
