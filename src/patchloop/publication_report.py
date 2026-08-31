from __future__ import annotations

from collections.abc import Mapping
from html import escape
from typing import cast

from patchloop.sanitize import redact_text
from patchloop.workflow_artifacts import ArtifactIntegrityError


def pull_request_body(report: Mapping[str, object], issue_number: int) -> str:
    verification = _mapping(report.get("verification"), "verification report")
    status = verification.get("status")
    status_text = {
        "checks_passed": "✅ PatchLoop checks passed.",
        "checks_failed": "⚠️ PatchLoop checks failed; unresolved failures remain.",
        "budget_exhausted": "⚠️ PatchLoop execution budget was exhausted; this patch may be incomplete.",
    }[cast(str, status)]
    changed = _mapping(report.get("changed_files"), "changed-files report")
    paths = cast(list[object], changed["paths"])
    verification_checks = cast(list[object], verification.get("checks", []))
    check_lines = [
        f"- `{_safe_pr_inline_text(_mapping(item, 'check result').get('command'))}`: "
        f"{_safe_pr_inline_text(_mapping(item, 'check result').get('status'))}"
        for item in verification_checks
    ] or ["- No individual check result was recorded."]
    budgets = _mapping(report.get("budgets"), "budget report")
    usage = _mapping(budgets.get("usage"), "budget usage")
    limits = _mapping(budgets.get("limits"), "budget limits")
    errors = cast(list[object], report.get("errors", []))
    unresolved = [
        f"- Check `{_safe_pr_inline_text(check.get('command'))}` remains failed"
        + (
            f" ({_safe_pr_inline_text(check.get('failure_category'))})."
            if check.get("failure_category") is not None
            else "."
        )
        for item in verification_checks
        if (check := _mapping(item, "check result")).get("status") == "failed"
    ] + [
        f"- {_safe_pr_inline_text(_mapping(item, 'error').get('code'))}: "
        f"{_safe_pr_inline_text(_mapping(item, 'error').get('message'))}"
        for item in errors
    ]
    if not unresolved:
        unresolved = ["- None reported by PatchLoop."]
    return "\n".join(
        [
            "## PatchLoop Draft",
            "",
            status_text,
            "",
            f"Relates to #{issue_number}.",
            "",
            "### Change summary",
            "",
            _safe_pr_inline_text(report.get("summary")),
            "",
            "### Changed files",
            "",
            *[f"- `{_safe_pr_inline_text(path)}`" for path in paths],
            "",
            "### PatchLoop checks",
            "",
            *check_lines,
            "",
            "### Execution",
            "",
            f"- Iterations: {_ratio(usage, limits, 'iterations', 'max_iterations')}",
            f"- Tool calls: {_ratio(usage, limits, 'tool_calls', 'max_tool_calls')}",
            f"- Changed files: {_ratio(usage, limits, 'changed_files', 'max_changed_files')}",
            f"- Diff lines: {_ratio(usage, limits, 'diff_lines', 'max_diff_lines')}",
            f"- Wall time (minutes): {_ratio(usage, limits, 'wall_time_minutes', 'max_wall_time_minutes')}",
            "",
            "### Unresolved failures",
            "",
            *unresolved,
            "",
            "PatchLoop checks cover only the repository-configured commands and are not a substitute for the repository's complete CI.",
            "This pull request remains a Draft and was not marked ready, approved, or merged by PatchLoop.",
        ]
    ) + "\n"


def _ratio(
    usage: Mapping[str, object],
    limits: Mapping[str, object],
    used: str,
    maximum: str,
) -> str:
    return (
        f"{_safe_pr_inline_text(usage.get(used))} / "
        f"{_safe_pr_inline_text(limits.get(maximum))}"
    )


def _safe_pr_inline_text(value: object) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ArtifactIntegrityError("Run report contains invalid publication text.")
    text = " ".join(redact_text(str(value)).splitlines())
    return escape(text, quote=False).replace("`", "&#96;")[:500]


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"Artifact {label} must be an object.")
    return cast(dict[str, object], value)
