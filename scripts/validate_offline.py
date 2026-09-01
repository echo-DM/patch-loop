from __future__ import annotations

import json
import os
import shutil
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
        run_step("Docker daemon", ["docker", "info", "--format", "{{.ServerVersion}}"])
        run_step(
            "Docker fixture image",
            ["docker", "image", "inspect", DOCKER_IMAGE, "--format", "{{.Id}}"],
        )
        run_step("Locked dependencies", ["uv", "lock", "--check"])
        run_step("Strict type checking", ["uv", "run", "mypy"])
        run_step("Offline acceptance suite", ["uv", "run", "pytest"])
        with tempfile.TemporaryDirectory(prefix="patchloop-release-") as temporary:
            validate_built_cli(Path(temporary))
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
    except (json.JSONDecodeError, RuntimeError) as error:
        print(f"Offline validation failed: {error}", file=sys.stderr)
        return 1
    print("PatchLoop offline validation passed.")
    return 0


def validate_built_cli(temporary: Path) -> None:
    distribution = temporary / "dist"
    run_step(
        "Wheel and source distribution",
        ["uv", "build", "--out-dir", str(distribution)],
    )
    wheels = tuple(distribution.glob("patchloop-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one PatchLoop wheel.")

    environment = temporary / "venv"
    run_step(
        "Isolated wheel environment",
        ["uv", "venv", "--python", sys.executable, str(environment)],
    )
    run_step(
        "Install built wheel",
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(environment / "bin" / "python"),
            str(wheels[0]),
        ],
    )
    license_expression = subprocess.run(
        [
            str(environment / "bin" / "python"),
            "-c",
            (
                "from importlib.metadata import metadata; "
                "print(metadata('patchloop').get('License-Expression', ''))"
            ),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if license_expression != "MIT":
        raise RuntimeError("Built wheel does not declare the MIT license.")
    repository = temporary / "target"
    repository.mkdir()
    repository.joinpath("README.md").write_text("Offline wheel validation.\n")
    shutil.copyfile(ROOT / "examples" / "patchloop.yml", repository / ".patchloop.yml")
    completed = subprocess.run(
        [
            str(environment / "bin" / "patchloop"),
            "run",
            "--task",
            str(ROOT / "examples" / "local-task.json"),
            "--repository",
            str(repository),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(completed.stdout)
    if not isinstance(report, dict):
        raise RuntimeError("Installed CLI did not emit a JSON object.")
    if report.get("terminal_outcome") != "no_change":
        raise RuntimeError("Installed CLI did not emit the expected no-change report.")


if __name__ == "__main__":
    raise SystemExit(main())
