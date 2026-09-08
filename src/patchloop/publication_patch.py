from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from patchloop.controlled_tools import (
    MAX_PATCHED_FILE_BYTES,
    PROTECTED_PATHS,
    PROTECTED_PREFIXES,
    diff_line_count,
)
from patchloop.sanitize import redact_text
from patchloop.verifier_policy import is_sensitive_env_name
from patchloop.workflow_artifacts import ArtifactIntegrityError


_HUNK_HEADER = re.compile(
    r"@@ -(0|[1-9][0-9]*)(?:,([0-9]+))? \+(0|[1-9][0-9]*)(?:,([0-9]+))? @@(?:[^\n]*)\n\Z"
)


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_count: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class FilePatch:
    path: str
    hunks: tuple[Hunk, ...]


def validate_patch(
    report: Mapping[str, object], patch: str
) -> tuple[FilePatch, ...]:
    if redact_text(patch) != patch:
        raise ArtifactIntegrityError("Patch contains credential-shaped content.")
    patch_report = _mapping(report.get("patch"), "patch report")
    if patch_report.get("format") != "unified_diff":
        raise ArtifactIntegrityError("Publish supports only unified-diff patches.")
    if patch_report.get("byte_length") != len(patch.encode()):
        raise ArtifactIntegrityError("Patch byte length does not match the run report.")
    parsed = _parse_patch(patch)
    changed = _mapping(report.get("changed_files"), "changed-files report")
    paths = changed.get("paths")
    if (
        not isinstance(paths, list)
        or paths != [item.path for item in parsed]
        or changed.get("count") != len(parsed)
    ):
        raise ArtifactIntegrityError("Patch paths do not match the run report.")
    budgets = _mapping(report.get("budgets"), "budget report")
    limits = _mapping(budgets.get("limits"), "budget limits")
    max_files = _positive_int(limits.get("max_changed_files"), "changed-file limit")
    max_lines = _positive_int(limits.get("max_diff_lines"), "diff-line limit")
    max_bytes = _positive_int(limits.get("max_file_bytes"), "file-size limit")
    usage = _mapping(budgets.get("usage"), "budget usage")
    if (
        usage.get("changed_files") != len(parsed)
        or usage.get("diff_lines") != diff_line_count(patch)
    ):
        raise ArtifactIntegrityError("Patch usage does not match the run report.")
    if len(parsed) > min(max_files, 20):
        raise ArtifactIntegrityError("Patch exceeds the changed-file safety limit.")
    if diff_line_count(patch) > min(max_lines, 2_000):
        raise ArtifactIntegrityError("Patch exceeds the diff-line safety limit.")
    if max_bytes > MAX_PATCHED_FILE_BYTES:
        raise ArtifactIntegrityError("Artifact file-size limit exceeds the safety ceiling.")
    return parsed


def apply_file_patch(file_patch: FilePatch, original: bytes | None) -> bytes:
    try:
        original_text = "" if original is None else original.decode()
    except UnicodeDecodeError as error:
        raise ArtifactIntegrityError("Patch base file is not UTF-8 text.") from error
    original_lines = original_text.splitlines(keepends=True)
    result: list[str] = []
    cursor = 0
    for hunk in file_patch.hunks:
        start = 0 if hunk.old_start == 0 else hunk.old_start - 1
        if start < cursor or start > len(original_lines):
            raise ArtifactIntegrityError("Patch hunk does not match the base file.")
        result.extend(original_lines[cursor:start])
        cursor = start
        for line in hunk.lines:
            content = line[1:]
            if line[0] in {" ", "-"}:
                if cursor >= len(original_lines) or original_lines[cursor] != content:
                    raise ArtifactIntegrityError(
                        "Patch content does not match the base file."
                    )
                cursor += 1
            if line[0] in {" ", "+"}:
                result.append(content)
    result.extend(original_lines[cursor:])
    encoded = "".join(result).encode()
    if len(encoded) > MAX_PATCHED_FILE_BYTES:
        raise ArtifactIntegrityError("Patched file exceeds the safety size limit.")
    if redact_text(encoded.decode()) != encoded.decode():
        raise ArtifactIntegrityError("Patched file contains credential-shaped content.")
    return encoded


def _parse_patch(patch: str) -> tuple[FilePatch, ...]:
    lines = patch.splitlines(keepends=True)
    parsed: list[FilePatch] = []
    index = 0
    while index < len(lines):
        old_header = lines[index]
        if not old_header.startswith("--- a/") or not old_header.endswith("\n"):
            raise ArtifactIntegrityError("Patch has an invalid file header.")
        old_path = old_header[6:-1]
        index += 1
        if index >= len(lines):
            raise ArtifactIntegrityError("Patch is missing its new-file header.")
        new_header = lines[index]
        if not new_header.startswith("+++ b/") or not new_header.endswith("\n"):
            raise ArtifactIntegrityError("Patch has an invalid file header.")
        new_path = new_header[6:-1]
        if old_path != new_path:
            raise ArtifactIntegrityError("Patch cannot rename files during Publish.")
        _validate_path(old_path)
        index += 1
        hunks: list[Hunk] = []
        while index < len(lines) and not lines[index].startswith("--- a/"):
            header = _HUNK_HEADER.fullmatch(lines[index])
            if header is None:
                raise ArtifactIntegrityError("Patch has an invalid hunk header.")
            old_start = int(header.group(1))
            old_count = int(header.group(2) or "1")
            new_count = int(header.group(4) or "1")
            index += 1
            hunk_lines: list[str] = []
            seen_old = 0
            seen_new = 0
            while (
                index < len(lines)
                and lines[index][:1] in {" ", "+", "-"}
                and (seen_old < old_count or seen_new < new_count)
            ):
                line = lines[index]
                hunk_lines.append(line)
                if line[0] in {" ", "-"}:
                    seen_old += 1
                if line[0] in {" ", "+"}:
                    seen_new += 1
                index += 1
            if seen_old != old_count or seen_new != new_count:
                raise ArtifactIntegrityError("Patch hunk line counts are inconsistent.")
            hunks.append(Hunk(old_start, old_count, new_count, tuple(hunk_lines)))
        if not hunks:
            raise ArtifactIntegrityError("Patch file has no change hunks.")
        parsed.append(FilePatch(old_path, tuple(hunks)))
    if not parsed or len({item.path for item in parsed}) != len(parsed):
        raise ArtifactIntegrityError("Patch must declare each changed file exactly once.")
    return tuple(parsed)


def _validate_path(path: str) -> None:
    candidate = PurePosixPath(path)
    name = candidate.name
    if (
        not path
        or "\\" in path
        or candidate.is_absolute()
        or candidate.as_posix() != path
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or is_sensitive_env_name(name)
        or path in PROTECTED_PATHS
        or path.startswith(PROTECTED_PREFIXES)
    ):
        raise ArtifactIntegrityError("Patch contains an unsafe or protected path.")


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArtifactIntegrityError(f"Artifact {label} is invalid.")
    return value


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"Artifact {label} must be an object.")
    return cast(dict[str, object], value)
