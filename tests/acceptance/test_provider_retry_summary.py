from __future__ import annotations

from pathlib import Path
import pytest

from patchloop.workflow_artifacts import ArtifactIntegrityError, append_run_summary


def report_fixture() -> dict[str, object]:
    return {
        "terminal_outcome": "pr_created", "verification": {"status": "checks_passed"},
        "budgets": {
            "usage": {"iterations": 1, "tool_calls": 2, "changed_files": 1, "diff_lines": 2, "wall_time_minutes": 0},
            "limits": {"max_iterations": 3, "max_tool_calls": 60, "max_changed_files": 20, "max_diff_lines": 2000, "max_wall_time_minutes": 30},
        },
    }


def test_actions_summary_shows_request_recovery_separately_from_task_success(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    report = report_fixture()
    report["provider_retry"] = {"attempts": 2, "waiting_seconds": 15.5, "recovered_requests": 1}

    append_run_summary(summary, report)

    text = summary.read_text()
    assert "| Terminal outcome | pr_created |" in text
    assert "| Provider retry attempts | 2 / 6 |" in text
    assert "| Retry waiting time (seconds) | 15.500 / 60 |" in text
    assert "| Recovered provider requests | 1 |" in text
    assert "Request recovery does not establish task success" in text


@pytest.mark.parametrize("reason", [
    "request_retry_limit", "run_retry_limit", "retry_wait_limit", "non_retryable",
    "run_deadline", "provider_delay_wait_limit", "provider_delay_run_deadline",
])
def test_actions_summary_shows_only_recognized_recovery_stop_reasons(tmp_path: Path, reason: str) -> None:
    report = report_fixture()
    report["terminal_outcome"] = "failed"
    report["provider_retry"] = {
        "attempts": 3, "waiting_seconds": 35, "recovered_requests": 1,
        "stop_reason": reason, "raw_provider_message": "untrusted-provider-text",
    }
    summary = tmp_path / "summary.md"

    append_run_summary(summary, report)

    text = summary.read_text()
    assert "| Terminal outcome | failed |" in text
    assert f"| Provider recovery stopped | {reason} |" in text
    assert "untrusted-provider-text" not in text


def test_old_report_without_retry_summary_keeps_existing_actions_rows(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"

    append_run_summary(summary, report_fixture())

    text = summary.read_text()
    assert "| Tool calls | 2 / 60 |" in text
    assert "| Checks | checks_passed |" in text
    assert "Provider retry" not in text


def test_no_retry_summary_displays_zero_usage(tmp_path: Path) -> None:
    report = report_fixture()
    report["provider_retry"] = {"attempts": 0, "waiting_seconds": 0, "recovered_requests": 0}
    summary = tmp_path / "summary.md"

    append_run_summary(summary, report)

    assert "| Provider retry attempts | 0 / 6 |" in summary.read_text()
    assert "| Retry waiting time (seconds) | 0.000 / 60 |" in summary.read_text()
    assert "Provider recovery stopped" not in summary.read_text()


@pytest.mark.parametrize(("field", "value"), [
    ("attempts", True), ("attempts", -1), ("attempts", 7), ("attempts", "raw-provider"),
    ("attempts", 2.0), ("recovered_requests", False), ("recovered_requests", 3),
    ("waiting_seconds", -0.5), ("waiting_seconds", float("nan")),
    ("waiting_seconds", float("inf")), ("waiting_seconds", 1e308),
    ("waiting_seconds", True), ("waiting_seconds", "raw-provider"),
    ("stop_reason", "untrusted | text\n<script>"), ("stop_reason", None),
    ("stop_reason", "x" * 1000),
])
def test_invalid_retry_metadata_is_rejected_before_summary_is_written(tmp_path: Path, field: str, value: object) -> None:
    report = report_fixture()
    retry: dict[str, object] = {"attempts": 2, "waiting_seconds": 15, "recovered_requests": 1}
    retry[field] = value
    report["provider_retry"] = retry
    summary = tmp_path / "summary.md"
    summary.write_text("Previous summary.\n")

    with pytest.raises(ArtifactIntegrityError, match="provider retry"):
        append_run_summary(summary, report)

    assert summary.read_text() == "Previous summary.\n"


@pytest.mark.parametrize("retry", [None, [], {}, {"attempts": 0, "waiting_seconds": 0}])
def test_missing_or_malformed_retry_fields_are_rejected(tmp_path: Path, retry: object) -> None:
    report = report_fixture()
    report["provider_retry"] = retry

    with pytest.raises(ArtifactIntegrityError):
        append_run_summary(tmp_path / "summary.md", report)


def test_measured_wait_overrun_is_visible_without_increasing_allowance(tmp_path: Path) -> None:
    report = report_fixture()
    report["terminal_outcome"] = "failed"
    report["provider_retry"] = {
        "attempts": 0, "waiting_seconds": 61, "recovered_requests": 0,
        "stop_reason": "retry_wait_limit",
    }
    summary = tmp_path / "summary.md"

    append_run_summary(summary, report)

    assert "| Retry waiting time (seconds) | 61.000 / 60 |" in summary.read_text()
