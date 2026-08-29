from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping

from patchloop.adapters import (
    ToolCall,
    ToolDefinition,
    ToolResult,
    VerificationRequest,
    VerificationResult,
    VerifierAdapter,
)
from patchloop.sanitize import redact_text


@dataclass(frozen=True)
class PatchBundle:
    format: str
    content: str
    sha256: str
    byte_length: int
    changed_files: tuple[str, ...]


@dataclass(frozen=True)
class _ToolRegistration:
    definition: ToolDefinition
    handler: Callable[[Mapping[str, object]], object]


class ControlledTools:
    """Validate and execute the complete model-visible capability set."""

    def __init__(
        self,
        repository: Path,
        checks: tuple[str, ...],
        verifier: VerifierAdapter,
    ) -> None:
        self._repository = repository.resolve()
        self._checks = checks
        self._verifier = verifier
        self._seen_call_ids: set[str] = set()
        self._original_files: dict[str, bytes | None] = {}
        self.latest_verification: VerificationResult | None = None
        self.verified_patch_sha256: str | None = None
        registrations = (
            _ToolRegistration(
                ToolDefinition(
                    "list_files", "List workspace files below a path.", (), ("path",)
                ),
                self._list_files,
            ),
            _ToolRegistration(
                ToolDefinition(
                    "search_code",
                    "Search text files for an exact string.",
                    ("query",),
                    ("path",),
                ),
                self._search_code,
            ),
            _ToolRegistration(
                ToolDefinition(
                    "read_file", "Read one UTF-8 workspace file.", ("path",)
                ),
                self._read_file,
            ),
            _ToolRegistration(
                ToolDefinition(
                    "apply_patch",
                    "Replace one UTF-8 workspace file.",
                    ("path", "content"),
                ),
                self._apply_patch,
            ),
            _ToolRegistration(
                ToolDefinition("inspect_diff", "Inspect the current unified diff.", ()),
                self._inspect_diff,
            ),
            _ToolRegistration(
                ToolDefinition(
                    "run_checks", "Run configured checks through the verifier.", ()
                ),
                self._run_checks,
            ),
        )
        self._registrations = {
            registration.definition.name: registration
            for registration in registrations
        }

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            registration.definition for registration in self._registrations.values()
        )

    def execute(self, call: ToolCall) -> ToolResult:
        if not call.id or call.id in self._seen_call_ids:
            return self._error(call, "duplicate_tool_call", "Tool call ids must be unique.")
        self._seen_call_ids.add(call.id)
        registration = self._registrations.get(call.name)
        if registration is None:
            return self._error(
                call,
                "unknown_tool",
                f"Unknown controlled tool: {redact_text(call.name)}.",
            )
        try:
            self._validate_arguments(
                call.arguments,
                registration.definition.required_arguments,
                registration.definition.optional_arguments,
            )
            output = registration.handler(call.arguments)
        except ToolInputError as error:
            return self._error(call, error.code, error.message)
        except OSError:
            return self._error(
                call,
                "tool_io_error",
                f"The {call.name} tool could not access the requested workspace path.",
            )
        return ToolResult(call.id, call.name, True, output=output)

    def patch_bundle(self) -> PatchBundle | None:
        content, changed_files = self._diff()
        if not content:
            return None
        encoded = content.encode()
        return PatchBundle(
            format="unified_diff",
            content=content,
            sha256=hashlib.sha256(encoded).hexdigest(),
            byte_length=len(encoded),
            changed_files=changed_files,
        )

    def _list_files(self, arguments: Mapping[str, object]) -> object:
        raw_path = arguments.get("path", ".")
        path = self._resolve_existing_path(raw_path, allow_root=True)
        if path.is_file():
            return [path.relative_to(self._repository).as_posix()]
        files: list[str] = []
        for root, directories, names in os.walk(path, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if name != ".git" and not (Path(root) / name).is_symlink()
            ]
            files.extend(
                (Path(root) / name).relative_to(self._repository).as_posix()
                for name in names
                if not (Path(root) / name).is_symlink()
            )
        return sorted(files)

    def _search_code(self, arguments: Mapping[str, object]) -> object:
        query = self._non_empty_string(arguments["query"], "query")
        path = self._resolve_existing_path(arguments.get("path", "."), allow_root=True)
        candidates = [path] if path.is_file() else [
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file()
            and not candidate.is_symlink()
            and ".git" not in candidate.relative_to(self._repository).parts
        ]
        matches: list[dict[str, object]] = []
        for candidate in candidates:
            try:
                lines = candidate.read_text().splitlines()
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if query in line:
                    matches.append(
                        {
                            "path": candidate.relative_to(self._repository).as_posix(),
                            "line": line_number,
                            "text": line,
                        }
                    )
        return matches

    def _read_file(self, arguments: Mapping[str, object]) -> object:
        path = self._resolve_existing_path(arguments["path"])
        if not path.is_file():
            raise ToolInputError("invalid_path", "read_file requires a regular file.")
        try:
            return path.read_text()
        except UnicodeDecodeError as error:
            raise ToolInputError(
                "non_text_file", "read_file only supports UTF-8 text files."
            ) from error

    def _apply_patch(self, arguments: Mapping[str, object]) -> object:
        content = self._string(arguments["content"], "content")
        if redact_text(content) != content:
            raise ToolInputError(
                "credential_content_rejected",
                "Patch content must not contain credential-shaped values.",
            )
        path = self._resolve_write_path(arguments["path"])
        if path.exists() and not path.is_file():
            raise ToolInputError("invalid_path", "apply_patch requires a regular file path.")
        if path.exists():
            try:
                existing = path.read_text()
            except UnicodeDecodeError as error:
                raise ToolInputError(
                    "non_text_file", "apply_patch only supports UTF-8 text files."
                ) from error
            if redact_text(existing) != existing:
                raise ToolInputError(
                    "credential_content_rejected",
                    "Patch inputs must not contain credential-shaped values.",
                )
        relative_path = path.relative_to(self._repository).as_posix()
        if relative_path not in self._original_files:
            self._original_files[relative_path] = path.read_bytes() if path.exists() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._replace_text(path, content)
        return {"path": relative_path, "applied": True}

    def _inspect_diff(self, arguments: Mapping[str, object]) -> object:
        content, changed_files = self._diff()
        return {"content": content, "changed_files": list(changed_files)}

    def _run_checks(self, arguments: Mapping[str, object]) -> object:
        _ = arguments
        candidate = self.patch_bundle()
        self.verified_patch_sha256 = candidate.sha256 if candidate is not None else None
        self.latest_verification = self._verifier.verify(
            VerificationRequest(self._repository, self._checks)
        )
        return {
            "status": self.latest_verification.status,
            "checks": [
                {
                    "command": check.command,
                    "status": check.status,
                    "exit_code": check.exit_code,
                    "output": redact_text(check.output),
                }
                for check in self.latest_verification.checks
            ],
        }

    def _diff(self) -> tuple[str, tuple[str, ...]]:
        changed_files = tuple(
            sorted(
                path
                for path, before in self._original_files.items()
                if before != (self._repository / path).read_bytes()
            )
        )
        chunks: list[str] = []
        for path in changed_files:
            before = self._decode_snapshot(self._original_files[path], path)
            after = self._decode_snapshot((self._repository / path).read_bytes(), path)
            chunks.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
        return "".join(chunks), changed_files

    @staticmethod
    def _replace_text(path: Path, content: str) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".patchloop-",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
            if path.exists():
                os.chmod(temporary, path.stat(follow_symlinks=False).st_mode & 0o777)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _decode_snapshot(value: bytes | None, path: str) -> str:
        if value is None:
            return ""
        try:
            return value.decode()
        except UnicodeDecodeError as error:
            raise ToolInputError(
                "non_text_file", f"Cannot create a text patch for {path}."
            ) from error

    def _resolve_existing_path(self, value: object, *, allow_root: bool = False) -> Path:
        path = self._resolve_relative_path(value, allow_root=allow_root)
        if not path.exists() or path.is_symlink():
            raise ToolInputError("invalid_path", "The requested workspace path is invalid.")
        return path

    def _resolve_write_path(self, value: object) -> Path:
        path = self._resolve_relative_path(value)
        relative = path.relative_to(self._repository)
        cursor = self._repository
        for part in relative.parts[:-1]:
            cursor /= part
            if cursor.is_symlink():
                raise ToolInputError(
                    "symlink_escape", "Patch paths must not traverse symbolic links."
                )
        if path.is_symlink():
            raise ToolInputError(
                "symlink_escape", "Patch paths must not target symbolic links."
            )
        return path

    def _resolve_relative_path(self, value: object, *, allow_root: bool = False) -> Path:
        raw = self._string(value, "path")
        if "\\" in raw:
            raise ToolInputError("unsafe_path", "Workspace paths must use POSIX separators.")
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts:
            raise ToolInputError(
                "unsafe_path", "Workspace paths must be normalized relative paths."
            )
        if not allow_root and relative == PurePosixPath("."):
            raise ToolInputError("unsafe_path", "A file path is required.")
        candidate = self._repository.joinpath(*relative.parts)
        try:
            candidate.resolve(strict=False).relative_to(self._repository)
        except ValueError as error:
            raise ToolInputError(
                "unsafe_path", "Workspace paths must remain inside the repository."
            ) from error
        return candidate

    @staticmethod
    def _validate_arguments(
        arguments: Mapping[str, object],
        required: tuple[str, ...],
        optional: tuple[str, ...] = (),
    ) -> None:
        if not all(isinstance(key, str) for key in arguments):
            raise ToolInputError("invalid_arguments", "Tool argument names must be strings.")
        keys = set(arguments)
        missing = set(required) - keys
        unexpected = keys - set(required) - set(optional)
        if missing or unexpected:
            raise ToolInputError(
                "invalid_arguments",
                "Tool arguments do not match the registered structured schema.",
            )

    @staticmethod
    def _string(value: object, name: str) -> str:
        if not isinstance(value, str):
            raise ToolInputError("invalid_arguments", f"{name} must be a string.")
        return value

    @classmethod
    def _non_empty_string(cls, value: object, name: str) -> str:
        result = cls._string(value, name)
        if not result:
            raise ToolInputError("invalid_arguments", f"{name} must not be empty.")
        return result

    @staticmethod
    def _error(call: ToolCall, code: str, message: str) -> ToolResult:
        return ToolResult(
            call.id,
            redact_text(call.name),
            False,
            error={"code": code, "message": redact_text(message)},
        )


class ToolInputError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
