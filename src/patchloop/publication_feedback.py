from __future__ import annotations

from collections.abc import Mapping
from html import escape
import re
from typing import Literal

from patchloop.sanitize import redact_text
from patchloop.workflow_artifacts import ArtifactIntegrityError


_MARKDOWN = re.compile(r"([\\`*_{}\[\]()<>#+|])")


def no_patch_feedback(
    report: Mapping[str, object], publication: Mapping[str, object]
) -> str:
    """Render bounded Issue feedback from validated, observable report fields."""
    outcome = validate_no_patch_outcome(report, publication)
    if outcome == "no_change":
        return "\n".join(
            [
                "## PatchLoop: No code change needed",
                "",
                _safe_text(report.get("summary")),
                "",
                _safe_text(report.get("actionable_message")),
                "",
                "No branch or pull request was created.",
            ]
        ) + "\n"
    if outcome == "needs_clarification":
        questions = _questions(publication)
        return "\n".join(
            [
                "## PatchLoop: Needs clarification",
                "",
                (
                    "PatchLoop needs the following information before it can make "
                    "a safe change:"
                ),
                "",
                *[f"- {_safe_text(question, limit=240)}" for question in questions],
                "",
                (
                    "After answering, remove and re-add the `patchloop` label to "
                    "run PatchLoop again."
                ),
                "No branch or pull request was created.",
            ]
        ) + "\n"
    if outcome == "failed":
        heading, instruction = _failure_template(report)
        error = _primary_error(report)
        return "\n".join(
            [
                f"## PatchLoop: {heading}",
                "",
                instruction,
                f"Result code: `{_safe_code(error.get('code'))}`.",
                "",
                "No branch or pull request was created.",
            ]
        ) + "\n"
    raise ArtifactIntegrityError("Run report has an unsupported no-patch outcome.")


def validate_no_patch_outcome(
    report: Mapping[str, object], publication: Mapping[str, object]
) -> Literal["no_change", "needs_clarification", "failed"]:
    if report.get("terminal_outcome") == "no_change" and publication == {
        "intent": "none",
        "reason": "no_change",
    }:
        return "no_change"
    if report.get("terminal_outcome") == "needs_clarification":
        questions = _questions(publication)
        if publication == {
            "intent": "issue_feedback",
            "reason": "needs_clarification",
            "questions": questions,
        } and report.get("clarification_questions") == questions:
            return "needs_clarification"
    if report.get("terminal_outcome") == "failed" and publication == {
        "intent": "none",
        "reason": "failed",
    }:
        _primary_error(report)
        return "failed"
    raise ArtifactIntegrityError("Run report has an invalid no-patch publication intent.")


def _failure_template(report: Mapping[str, object]) -> tuple[str, str]:
    error = _primary_error(report)
    category = error.get("category")
    code = error.get("code")
    verification = _mapping(report.get("verification"), "verification report")
    status = verification.get("status")
    if category == "authorization":
        return (
            "Run not authorized",
            (
                "The trigger was rejected before PatchLoop inspected or changed "
                "repository content."
            ),
        )
    if status in {"setup_failed", "infrastructure_failed"} or code in {
        "verifier_setup_failed",
        "verifier_infrastructure_failed",
    }:
        return (
            "Docker or setup error",
            (
                "The isolated verifier could not complete setup or infrastructure "
                "work. Check the workflow and verifier configuration, then retry."
            ),
        )
    if status == "budget_exhausted" or category == "budget":
        return (
            "Budget exhausted without a legal patch",
            (
                "PatchLoop reached a configured safety limit before it had a "
                "publishable patch. Narrow the request or adjust a lower repository "
                "budget, then retry."
            ),
        )
    if category == "configuration":
        return (
            "Configuration error",
            "PatchLoop could not use the repository configuration. Correct `.patchloop.yml`, then retry.",
        )
    if category == "model":
        return (
            "Model error",
            (
                "The configured model did not produce a usable result. Check the "
                "model configuration or retry the run."
            ),
        )
    if category == "infrastructure":
        return (
            "Infrastructure error",
            "The workflow infrastructure could not complete the run. Check the workflow logs and retry.",
        )
    return (
        "No publishable patch",
        "PatchLoop stopped safely without a legal patch. Review the result code before retrying.",
    )


def _questions(publication: Mapping[str, object]) -> list[str]:
    raw = publication.get("questions")
    if (
        not isinstance(raw, list)
        or not 1 <= len(raw) <= 3
        or not all(
            isinstance(question, str)
            and bool(question.strip())
            and len(question) <= 240
            for question in raw
        )
    ):
        raise ArtifactIntegrityError("Clarification feedback contains invalid questions.")
    return list(raw)


def _primary_error(report: Mapping[str, object]) -> dict[str, object]:
    errors = report.get("errors")
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        raise ArtifactIntegrityError("Failed run report has no valid error result.")
    error = errors[0]
    if not isinstance(error.get("category"), str) or not isinstance(
        error.get("code"), str
    ):
        raise ArtifactIntegrityError("Failed run report has an invalid error result.")
    return error


def _safe_code(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_]{1,100}", value):
        raise ArtifactIntegrityError("Run report contains an invalid result code.")
    return value


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"Artifact {label} must be an object.")
    return value


def _safe_text(value: object, *, limit: int = 500) -> str:
    if not isinstance(value, str):
        raise ArtifactIntegrityError("Run report contains invalid feedback text.")
    text = " ".join(redact_text(value).splitlines())[:limit]
    return _MARKDOWN.sub(r"\\\1", escape(text, quote=False))
