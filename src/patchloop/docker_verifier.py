from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

from patchloop.adapters import (
    CheckFailureCategory,
    CheckResult,
    VerificationRequest,
    VerificationResult,
)
from patchloop.sanitize import redact_text


ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template")


@dataclass(frozen=True)
class _CommandExecution:
    result: CheckResult
    infrastructure_failed: bool = False


class _BoundedOutput:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._content = bytearray()
        self.total_bytes = 0
        self.exceeded = threading.Event()

    @property
    def content(self) -> bytes:
        return bytes(self._content)

    def drain(self, stream: BinaryIO) -> None:
        while chunk := stream.read(8_192):
            self.total_bytes += len(chunk)
            remaining = self._limit - len(self._content)
            if remaining > 0:
                self._content.extend(chunk[:remaining])
            if self.total_bytes > self._limit:
                self.exceeded.set()


class DockerVerifierAdapter:
    """Run repository-declared commands in a constrained disposable Docker copy."""

    def __init__(self, executable: str = "docker") -> None:
        self._executable = executable

    def verify(self, request: VerificationRequest) -> VerificationResult:
        with tempfile.TemporaryDirectory(prefix="patchloop-verifier-") as temporary:
            workspace = Path(temporary) / "repository"
            try:
                shutil.copytree(
                    request.repository,
                    workspace,
                    symlinks=True,
                    ignore=_ignored_verifier_inputs,
                )
                _restore_baseline(workspace, request)
                _apply_candidate_patch(workspace, request)
            except (OSError, subprocess.SubprocessError, ValueError):
                return VerificationResult(
                    status="infrastructure_failed",
                    checks=(),
                    setup=(
                        CheckResult(
                            "candidate-patch",
                            "failed",
                            125,
                            "The verifier could not prepare the candidate patch.",
                            "infrastructure",
                        ),
                    ),
                )

            setup_results: list[CheckResult] = []
            for index, command in enumerate(request.verifier.setup, start=1):
                execution = _run_docker_command(
                    workspace,
                    request,
                    f"setup-{index}",
                    command,
                    self._executable,
                )
                setup_results.append(execution.result)
                if execution.result.status == "failed":
                    status: Literal["setup_failed", "infrastructure_failed"] = (
                        "infrastructure_failed"
                        if execution.infrastructure_failed
                        else "setup_failed"
                    )
                    return VerificationResult(
                        status=status,
                        checks=(),
                        setup=tuple(setup_results),
                    )

            check_results: list[CheckResult] = []
            for index, command in enumerate(request.verifier.checks, start=1):
                execution = _run_docker_command(
                    workspace,
                    request,
                    f"check-{index}",
                    command,
                    self._executable,
                )
                check_results.append(execution.result)
                if execution.infrastructure_failed:
                    return VerificationResult(
                        status="infrastructure_failed",
                        checks=tuple(check_results),
                        setup=tuple(setup_results),
                    )

            check_status: Literal["checks_passed", "checks_failed"] = (
                "checks_passed"
                if all(result.status == "passed" for result in check_results)
                else "checks_failed"
            )
            return VerificationResult(
                status=check_status,
                checks=tuple(check_results),
                setup=tuple(setup_results),
            )


def _ignored_verifier_inputs(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if (
            name == ".git"
            or name == ".patchloop"
            or name.startswith(".patchloop-state")
            or _is_sensitive_env_name(name)
            or path.is_symlink()
        ):
            ignored.add(name)
    return ignored


def _is_sensitive_env_name(name: str) -> bool:
    return name == ".env" or (
        name.startswith(".env.") and not name.endswith(ENV_TEMPLATE_SUFFIXES)
    )


def _restore_baseline(workspace: Path, request: VerificationRequest) -> None:
    for snapshot in request.original_files:
        relative = PurePosixPath(snapshot.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid candidate patch path.")
        target = workspace.joinpath(*relative.parts)
        if snapshot.content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(snapshot.content)


def _apply_candidate_patch(workspace: Path, request: VerificationRequest) -> None:
    if not request.patch:
        return
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": "C",
        "LC_ALL": "C",
    }
    completed = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=workspace,
        env=environment,
        input=request.patch.encode(),
        capture_output=True,
        check=False,
        timeout=request.verifier.limits.timeout_seconds,
    )
    if completed.returncode != 0:
        raise ValueError("Candidate patch could not be applied.")


def _run_docker_command(
    workspace: Path,
    request: VerificationRequest,
    command_id: str,
    command: str,
    executable: str,
) -> _CommandExecution:
    limits = request.verifier.limits
    cidfile = workspace.parent / f"container-{uuid.uuid4().hex}.cid"
    arguments = [
        executable,
        "run",
        "--rm",
        "--cidfile",
        str(cidfile),
        "--memory",
        f"{limits.memory_mb}m",
        "--memory-swap",
        f"{limits.memory_mb}m",
        "--pids-limit",
        str(limits.pids),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--mount",
        f"type=bind,src={workspace},dst=/workspace",
        "--workdir",
        "/workspace",
        "--entrypoint",
        "/bin/sh",
        request.verifier.image,
        "-c",
        command,
    ]
    try:
        process = subprocess.Popen(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError:
        return _infrastructure_result(command_id, "Docker could not start the verifier.")

    assert process.stdout is not None
    output = _BoundedOutput(limits.output_bytes)
    reader = threading.Thread(target=output.drain, args=(process.stdout,), daemon=True)
    reader.start()
    deadline = time.monotonic() + limits.timeout_seconds
    termination: str | None = None
    while process.poll() is None:
        if output.exceeded.is_set():
            termination = "output_limit"
            break
        if time.monotonic() >= deadline:
            termination = "timeout"
            break
        time.sleep(0.01)
    if termination is not None:
        _kill_container(cidfile, executable)
        process.kill()
    return_code = process.wait()
    reader.join(timeout=1)
    cidfile.unlink(missing_ok=True)

    was_truncated = output.total_bytes > limits.output_bytes
    if was_truncated:
        rendered_output = "[output truncated]"
    else:
        rendered_output = redact_text(output.content.decode(errors="replace"))
    if termination == "timeout":
        return _CommandExecution(
            CheckResult(
                command_id,
                "failed",
                124,
                rendered_output,
                "timeout",
                was_truncated,
            )
        )
    if termination == "output_limit":
        return _CommandExecution(
            CheckResult(
                command_id,
                "failed",
                125,
                rendered_output,
                "output_limit",
                True,
            )
        )
    if return_code == 0:
        return _CommandExecution(
            CheckResult(command_id, "passed", 0, rendered_output, None, was_truncated)
        )
    if return_code in {125, 126}:
        return _infrastructure_result(
            command_id,
            "Docker could not execute the configured verifier image.",
            output=rendered_output,
            exit_code=return_code,
        )
    failure_category: CheckFailureCategory = (
        "memory_limit" if return_code == 137 else "command_failed"
    )
    if "resource temporarily unavailable" in rendered_output.casefold():
        failure_category = "process_limit"
    return _CommandExecution(
        CheckResult(
            command_id,
            "failed",
            return_code,
            rendered_output,
            failure_category,
            was_truncated,
        )
    )


def _kill_container(cidfile: Path, executable: str) -> None:
    try:
        container_id = cidfile.read_text().strip()
    except OSError:
        return
    if not container_id:
        return
    subprocess.run(
        [executable, "kill", container_id],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _infrastructure_result(
    command_id: str,
    message: str,
    *,
    output: str = "",
    exit_code: int = 125,
) -> _CommandExecution:
    rendered = output or message
    return _CommandExecution(
        CheckResult(
            command_id,
            "failed",
            exit_code,
            redact_text(rendered),
            "infrastructure",
        ),
        infrastructure_failed=True,
    )
