from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import httpx
from google.genai.errors import ClientError, ServerError
from langchain_google_genai.chat_models import GoogleAPIError, GoogleRateLimitError
from langchain_core.messages import ToolMessage

from patchloop.adapters import (
    DeterministicVerifierAdapter,
    EvaluationTask,
    ToolDefinition,
    VerificationResult,
)
from patchloop.config import load_repository_config
from patchloop.core import RunAdapters, RunRequest, TaskSnapshot, run
from patchloop.errors import ConfigError
from patchloop.gemini import GeminiPatchModelAdapter


@dataclass
class FakeResponse:
    content: object = ""
    tool_calls: list[dict[str, object]] | None = None
    invalid_tool_calls: list[dict[str, object]] | None = None


class FakeGeminiClient:
    def __init__(self, responses: list[FakeResponse | BaseException]) -> None:
        self._responses = iter(responses)
        self.bound_tools: list[dict[str, object]] = []
        self.requests: list[list[object]] = []
        self.timeouts: list[float] = []

    def bind_tools(self, tools: list[dict[str, object]]) -> FakeGeminiClient:
        self.bound_tools = tools
        return self

    def invoke(self, messages: list[object], *, timeout: float = 60.0) -> FakeResponse:
        self.requests.append(messages)
        self.timeouts.append(timeout)
        response = next(self._responses)
        if isinstance(response, BaseException):
            raise response
        return response


class RateLimitError(Exception):
    status_code = 429


class RetryClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds


def test_three_retries_recover_without_consuming_useful_model_turns(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    config_path = repository / ".patchloop.yml"
    config_path.write_text(config_path.read_text().replace("max_tool_calls: 60", "max_tool_calls: 2"))
    client = FakeGeminiClient([
        TimeoutError(), RateLimitError(), ConnectionError(),
        FakeResponse(tool_calls=[{"id": "edit", "name": "apply_patch", "args": {
            "path": "README.md", "content": "Recovered.\n",
        }}]),
        FakeResponse(tool_calls=[{"id": "checks", "name": "run_checks", "args": {}}]),
    ])
    clock = RetryClock()

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep, jitter=lambda: 0.5)

    assert result.terminal_outcome == "pr_created"
    assert result.report["errors"] == []
    assert clock.waits == [5.5, 10.5, 20.5]
    assert result.report["provider_retry"] == {
        "attempts": 3, "waiting_seconds": 36.5, "recovered_requests": 1,
    }
    assert result.report["budgets"]["usage"]["tool_calls"] == 2
    assert result.report["budgets"]["usage"]["iterations"] == 1
    assert client.requests[0] == client.requests[1] == client.requests[2] == client.requests[3]


def test_retry_exhaustion_after_edit_reports_last_failure_without_publication(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()
    client = FakeGeminiClient([
        FakeResponse(tool_calls=[{"id": "edit", "name": "apply_patch", "args": {
            "path": "README.md", "content": "Local edit.\n",
        }}]),
        TimeoutError(), ConnectionError(), TimeoutError(), RateLimitError(),
    ])

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep)

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication["intent"] == "none"
    assert result.report["errors"][0]["code"] == "provider_rate_limited"
    assert result.report["provider_retry"] == {
        "attempts": 3, "waiting_seconds": 35.0, "recovered_requests": 0,
        "stop_reason": "request_retry_limit",
    }
    assert clock.waits == [5, 10, 20]
    assert len(client.requests) == 5
    assert client.requests[1] == client.requests[2] == client.requests[3] == client.requests[4]
    assert len([m for m in client.requests[4] if isinstance(m, ToolMessage)]) == 1
    assert result.report["budgets"]["resource_limit_events"] == []


def test_success_resets_request_retries_but_run_limit_stays_shared(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    responses: list[FakeResponse | BaseException] = []
    for index in range(6):
        responses.extend([TimeoutError(), FakeResponse(tool_calls=[{
            "id": f"read-{index}", "name": "read_file", "args": {"path": "README.md"},
        }])])
    responses.append(RateLimitError())
    clock = RetryClock()
    client = FakeGeminiClient(responses)

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep)

    assert len(client.requests) == 13
    assert clock.waits == [5] * 6
    assert result.report["provider_retry"] == {
        "attempts": 6, "waiting_seconds": 30.0, "recovered_requests": 6,
        "stop_reason": "run_retry_limit",
    }
    assert result.report["budgets"]["usage"]["tool_calls"] == 6
    assert result.report["budgets"]["usage"]["iterations"] == 0


def test_shared_wait_budget_stops_before_shortening_fallback(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()
    client = FakeGeminiClient([
        TimeoutError(), TimeoutError(), TimeoutError(),
        FakeResponse(tool_calls=[{"id": "read", "name": "read_file", "args": {"path": "README.md"}}]),
        TimeoutError(), TimeoutError(), RateLimitError(),
    ])

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep, jitter=lambda: 1)

    assert clock.waits == [6, 11, 21, 6, 11]
    assert result.report["provider_retry"] == {
        "attempts": 5, "waiting_seconds": 55.0, "recovered_requests": 1,
        "stop_reason": "retry_wait_limit",
    }
    assert result.report["errors"][0]["code"] == "provider_rate_limited"


def test_only_jitter_is_trimmed_to_fit_the_shared_wait_budget(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()
    read = FakeResponse(tool_calls=[{"id": "read", "name": "read_file", "args": {"path": "README.md"}}])
    client = FakeGeminiClient([
        TimeoutError(), TimeoutError(), TimeoutError(), read,
        TimeoutError(), TimeoutError(), read,
        TimeoutError(), FakeResponse(tool_calls=[
            {"id": "edit", "name": "apply_patch", "args": {"path": "README.md", "content": "Done.\n"}},
            {"id": "check", "name": "run_checks", "args": {}},
        ]),
    ])

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep, jitter=lambda: 1)

    assert clock.waits == [6, 11, 21, 6, 11, 5]
    assert result.terminal_outcome == "pr_created"
    assert result.report["provider_retry"] == {
        "attempts": 6, "waiting_seconds": 60.0, "recovered_requests": 3,
    }


@pytest.mark.parametrize(("overshoot", "stop"), [(61, "retry_wait_limit"), (1800, "retry_wait_limit")])
def test_actual_wait_overrun_stops_before_an_additional_request(tmp_path: Path, overshoot: float, stop: str) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()
    client = FakeGeminiClient([TimeoutError(), FakeResponse(content="must not execute")])

    def oversleep(seconds: float) -> None:
        clock.waits.append(seconds)
        clock.now += overshoot

    result = run_gemini(repository, client, clock=clock, sleep=oversleep)

    assert len(client.requests) == 1
    assert result.report["provider_retry"] == {
        "attempts": 0, "waiting_seconds": overshoot, "recovered_requests": 0,
        "stop_reason": stop,
    }
    assert result.report["errors"][0]["category"] == "model"


def test_longest_provider_hint_replaces_fallback_wait(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    response = httpx.Response(429, headers={"Retry-After": "12"})
    error = ClientError(429, {"error": {"details": [{
        "@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7.5s",
    }]}}, response=response)
    wrapped = GoogleRateLimitError("raw-wrapper")
    wrapped.__cause__ = error
    client = FakeGeminiClient([wrapped, FakeResponse(tool_calls=[
        {"id": "edit", "name": "apply_patch", "args": {"path": "README.md", "content": "Done.\n"}},
        {"id": "check", "name": "run_checks", "args": {}},
    ])])
    clock = RetryClock()

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep, jitter=lambda: 0.5)

    assert clock.waits == [12.5]
    assert result.terminal_outcome == "pr_created"
    assert result.report["provider_retry"]["waiting_seconds"] == 12.5
    assert "raw-" not in json.dumps(result.report)


@pytest.mark.parametrize(("hint", "expected"), [
    ("0s", 0), ("1.25s", 1.25), ("-1s", 5), ("NaNs", 5), ("infs", 5),
    ("1e2s", 5), ("invalid", 5), (True, 5), (None, 5),
    ("9" * 129 + "s", 5), ("0.1234567891s", 5),
])
def test_retry_info_duration_is_bounded_and_invalid_hints_use_fallback(tmp_path: Path, hint: object, expected: float) -> None:
    repository = configured_repository(tmp_path)
    error = ClientError(429, {"error": {"details": [{
        "@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": hint,
    }]}})
    client = FakeGeminiClient([error, FakeResponse(content="Done")])
    clock = RetryClock()

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep)

    assert clock.waits == [expected]
    assert result.report["provider_retry"]["attempts"] == 1
    assert result.report["provider_retry"]["recovered_requests"] == 1
    # Obtaining a response is recovery, even when the task produces no patch.
    assert result.report["errors"][0]["code"] == "empty_patch"


@pytest.mark.parametrize(("header", "expected"), [
    ("3", 3), ("0", 0), ("1.5", 1.5),
    ("Wed, 14 Oct 2026 00:00:12 GMT", 12),
    ("Tue, 13 Oct 2026 23:59:59 GMT", 0),
    ("Wednesday, 14-Oct-26 00:00:12 GMT", 12),
    ("Wed Oct 14 00:00:12 2026", 12),
    ("Wed, 14 Oct 2026 00:00:12", 5), ("nonsense", 5),
    ("Wed, 14 Oct 2026 00:00:12 +0000", 5),
    ("bad, 14 Oct 2026 00:00:12 GMT", 5),
    ("14 Oct 2026 00:00:12 GMT", 5),
    ("Wed, 14 Oct 2026 00:00:12 PST", 5),
    ("nan", 5), ("inf", 5), ("-5", 5), ("1e9", 5),
])
def test_retry_after_supports_duration_and_dates_using_wall_clock(tmp_path: Path, header: str, expected: float) -> None:
    repository = configured_repository(tmp_path)
    error = ClientError(429, {}, response=httpx.Response(429, headers={"Retry-After": header}))
    client = FakeGeminiClient([error, FakeResponse(content="Done")])
    clock = RetryClock()

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep, wall_clock=lambda: 1791936000.0)

    assert clock.waits == [expected]
    assert result.report["provider_retry"]["attempts"] == 1


@pytest.mark.parametrize(("hint", "elapsed", "waits", "attempts", "reason"), [
    ("61s", 0, [], 0, "provider_delay_wait_limit"),
    ("10s", 1790, [], 0, "provider_delay_run_deadline"),
    ("20s", 0, [20, 20, 20], 3, "request_retry_limit"),
])
def test_provider_minimum_wait_cannot_be_shortened_after_edit(tmp_path: Path, hint: str, elapsed: float, waits: list[float], attempts: int, reason: str) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()
    error = ClientError(429, {"error": {"details": [{
        "@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": hint,
    }]}})

    class ElapsedClient(FakeGeminiClient):
        def invoke(self, messages: list[object], *, timeout: float = 60) -> FakeResponse:
            if self.requests and not clock.now:
                clock.now = elapsed
            return super().invoke(messages, timeout=timeout)

    client = ElapsedClient([
        FakeResponse(tool_calls=[{"id": "edit", "name": "apply_patch", "args": {"path": "README.md", "content": "Unverified.\n"}}]),
        error, error, error, error,
    ])

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep)

    assert clock.waits == waits
    assert result.report["provider_retry"]["attempts"] == attempts
    assert result.report["provider_retry"]["stop_reason"] == reason
    assert result.patch is None
    assert result.publication["intent"] == "none"
    assert result.report["errors"][0]["code"] == "provider_rate_limited"
    assert result.report["budgets"]["resource_limit_events"] == []


def test_each_request_timeout_shrinks_with_remaining_runtime(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()

    class SlowClient(FakeGeminiClient):
        def invoke(self, messages: list[object], *, timeout: float = 60) -> FakeResponse:
            if not self.requests:
                clock.now += 1780
            return super().invoke(messages, timeout=timeout)

    client = SlowClient([
        TimeoutError(),
        FakeResponse(tool_calls=[{"id": "edit", "name": "apply_patch", "args": {"path": "README.md", "content": "Done.\n"}}]),
        FakeResponse(tool_calls=[{"id": "check", "name": "run_checks", "args": {}}]),
    ])

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep)

    assert result.terminal_outcome == "pr_created"
    assert client.timeouts == [60, 15, 15]
    assert result.report["provider_retry"]["waiting_seconds"] == 5


def test_deadline_is_rechecked_immediately_before_waiting(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()
    client = FakeGeminiClient([TimeoutError(), FakeResponse(content="must not execute")])

    def delayed_jitter() -> float:
        clock.now = 1795
        return 0

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep, jitter=delayed_jitter)

    assert clock.waits == []
    assert len(client.requests) == 1
    assert result.report["provider_retry"] == {
        "attempts": 0, "waiting_seconds": 0, "recovered_requests": 0,
        "stop_reason": "run_deadline",
    }


def test_submillisecond_runtime_cannot_disable_sdk_request_timeout(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    clock = RetryClock()
    error = ClientError(429, {}, response=httpx.Response(429, headers={"Retry-After": "0"}))

    class NearlyExpiredClient(FakeGeminiClient):
        def invoke(self, messages: list[object], *, timeout: float = 60) -> FakeResponse:
            if self.requests:
                clock.now = 1799.9995
            return super().invoke(messages, timeout=timeout)

    client = NearlyExpiredClient([
        FakeResponse(tool_calls=[{"id": "edit", "name": "apply_patch", "args": {"path": "README.md", "content": "Unverified.\n"}}]),
        error,
        FakeResponse(tool_calls=[{"id": "checks", "name": "run_checks", "args": {}}]),
    ])

    result = run_gemini(repository, client, clock=clock, sleep=clock.sleep)

    assert client.timeouts == [60, 60]
    assert clock.waits == []
    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication["intent"] == "none"
    assert result.report["errors"][0]["code"] == "provider_rate_limited"
    assert result.report["provider_retry"]["attempts"] == 0
    assert result.report["provider_retry"]["stop_reason"] == "run_deadline"


def quota_failure(violation: object, *, details_shape: str = "body") -> ClientError:
    details = [{
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [violation],
    }]
    payload: object = {"error": {
        "status": "RESOURCE_EXHAUSTED", "message": "raw-quota-body", "details": details,
    }}
    error = ClientError(429, payload)
    if details_shape == "list":
        error.details = details
    elif details_shape == "flat":
        error.details = {"details": details}
    return error


@pytest.mark.parametrize("details_shape", ["body", "flat", "list"])
@pytest.mark.parametrize("violation", [
    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"},
    {"quotaId": "GenerateRequestsPerDayPerProjectPerModel"},
    {"quotaId": "GenerateContentInputTokensPerModelPerDay-FreeTier"},
    {"quotaId": "GenerateContentInputTokensPerModelPerDay"},
    {"quotaMetric": "generativelanguage.googleapis.com/generate_requests_per_model_per_day"},
])
def test_confirmed_daily_quota_exhaustion_stops_the_pending_request(
    tmp_path: Path, details_shape: str, violation: object, caplog: pytest.LogCaptureFixture,
) -> None:
    repository = configured_repository(tmp_path)
    wrapped = GoogleRateLimitError("raw-quota-wrapper")
    wrapped.__cause__ = quota_failure(violation, details_shape=details_shape)
    client = FakeGeminiClient([wrapped, FakeResponse(content="must not execute")])

    result = run_gemini(repository, client)

    assert len(client.requests) == 1
    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.report["errors"] == [{
        "category": "model",
        "code": "provider_daily_quota_exhausted",
        "message": "Gemini's daily quota is exhausted. Check the project's quota in AI Studio and rerun after the daily allowance resets.",
    }]
    assert result.report["actionable_message"] == result.report["errors"][0]["message"]
    assert "raw-" not in json.dumps(result.report)
    assert "raw-" not in str(client.requests)
    assert "raw-" not in caplog.text


@pytest.mark.parametrize("details", [
    None,
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    {"error": {"details": "malformed"}},
    [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": "malformed"}],
    [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [None, {"quotaId": 123}]}],
    [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "UnknownPerDayQuota"}]}],
    [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}],
    [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}],
    [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "x" * 10_000}]}],
])
def test_ambiguous_or_malformed_quota_details_do_not_establish_daily_exhaustion(
    tmp_path: Path, details: object,
) -> None:
    repository = configured_repository(tmp_path)
    error = ClientError(429, {"error": {
        "status": "RESOURCE_EXHAUSTED",
        "message": "daily quota exhausted GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    }})
    error.details = details
    client = FakeGeminiClient([error] * 4)

    result = run_gemini(repository, client)

    assert len(client.requests) == 4
    assert result.report["errors"][0]["code"] == "provider_rate_limited"
    assert "daily quota exhausted" not in json.dumps(result.report)


@pytest.mark.parametrize("failure", [
    RuntimeError("raw-unknown"),
    ClientError(400, {}), ClientError(401, {}), ClientError(403, {}), ClientError(404, {}),
    ClientError(422, {}), ServerError(501, {}),
    httpx.LocalProtocolError("raw-invalid-protocol"),
    quota_failure({"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}),
])
def test_non_retryable_provider_failure_after_edit_never_publishes_the_patch(
    tmp_path: Path, failure: BaseException,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient([
        FakeResponse(tool_calls=[{"id": "edit", "name": "apply_patch", "args": {
            "path": "README.md", "content": "Unverified local edit.\n",
        }}]),
        failure,
        FakeResponse(tool_calls=[{"id": "checks", "name": "run_checks", "args": {}}]),
    ])

    result = run_gemini(repository, client)

    assert len(client.requests) == 2
    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.report["patch"] is None
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.verification["status"] == "not_run"
    assert result.report["errors"][0]["category"] == "model"
    assert result.report["budgets"]["resource_limit_events"] == []
    assert result.report["budgets"]["usage"]["tool_calls"] == 1
    assert "decision_after_edit" not in json.dumps(result.report)
    assert "raw-" not in json.dumps(result.report)


@pytest.mark.parametrize("chain_kind", ["cause", "context", "cycle", "suppressed"])
def test_provider_exception_chains_are_bounded_and_respect_suppressed_context(
    tmp_path: Path, chain_kind: str,
) -> None:
    repository = configured_repository(tmp_path)
    wrapper = RuntimeError("raw-wrapper")
    cause = TimeoutError("raw-timeout")
    if chain_kind == "cause":
        wrapper.__cause__ = cause
    elif chain_kind == "context":
        wrapper.__context__ = cause
    elif chain_kind == "suppressed":
        wrapper.__context__ = cause
        wrapper.__suppress_context__ = True
    else:
        wrapper.__cause__ = wrapper
    client = FakeGeminiClient([wrapper] * 4)

    result = run_gemini(repository, client)

    if chain_kind in {"cause", "context"}:
        assert len(client.requests) == 4
        assert result.report["errors"][0]["code"] == "provider_timeout"
    else:
        assert len(client.requests) == 1
        assert result.report["errors"][0]["code"] == "provider_error"
    assert "raw-" not in json.dumps(result.report)


def test_http_response_status_is_normalized_without_persisting_response_data(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    repository = configured_repository(tmp_path)
    response = httpx.Response(403, text="raw-body", headers={"x-raw": "raw-header"})
    error = httpx.HTTPStatusError(
        "raw-message", request=httpx.Request("POST", "https://example.invalid/raw-url"),
        response=response,
    )
    client = FakeGeminiClient([error])

    result = run_gemini(repository, client)

    assert len(client.requests) == 1
    assert result.report["errors"][0]["code"] == "provider_permission_denied"
    assert "raw-" not in json.dumps(result.report)
    assert "raw-" not in str(client.requests)
    assert "raw-" not in caplog.text


def test_sdk_rate_limit_metadata_confirms_daily_exhaustion_without_http_status(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    error = GoogleRateLimitError("raw-sdk-message")
    error.details = quota_failure({
        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    }).details
    client = FakeGeminiClient([error])

    result = run_gemini(repository, client)

    assert len(client.requests) == 1
    assert result.report["errors"][0]["code"] == "provider_daily_quota_exhausted"


@pytest.mark.parametrize("failure", [
    ClientError(401, {}),
    quota_failure({"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}),
    RuntimeError("raw-unknown"),
    TimeoutError("raw-timeout"),
])
def test_provider_failure_returned_after_deadline_does_not_publish_existing_edits(
    tmp_path: Path, failure: BaseException,
) -> None:
    repository = configured_repository(tmp_path)
    now = 0.0

    class DelayedFailureClient(FakeGeminiClient):
        def invoke(self, messages: list[object], *, timeout: float = 60.0) -> FakeResponse:
            nonlocal now
            if self.requests:
                now = 1801.0
            return super().invoke(messages, timeout=timeout)

    client = DelayedFailureClient([
        FakeResponse(tool_calls=[{"id": "edit", "name": "apply_patch", "args": {
            "path": "README.md", "content": "Unverified timed edit.\n",
        }}]),
        failure,
    ])

    result = run_gemini(repository, client, clock=lambda: now)

    assert len(client.requests) == 2
    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.verification["status"] == "not_run"
    assert result.report["errors"][0]["category"] == "model"
    assert result.report["budgets"]["usage"]["wall_time_minutes"] == 30
    assert result.report["budgets"]["resource_limit_events"] == []


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionError("raw-connection"),
        httpx.ConnectError("raw-connect"),
        httpx.ReadError("raw-read"),
        httpx.RemoteProtocolError("raw-disconnect"),
        httpx.ReadTimeout("raw-timeout"),
        ClientError(408, {"error": {"message": "raw-timeout"}}),
        ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}),
        GoogleRateLimitError("raw-rate-limit"),
        ServerError(500, {"error": {"message": "raw-server"}}),
        ServerError(502, {"error": {"message": "raw-server"}}),
        GoogleAPIError(503, {"error": {"message": "raw-server"}}),
        ServerError(504, {"error": {"message": "raw-server"}}),
    ],
)
def test_recognized_transient_provider_failure_can_recover(
    tmp_path: Path, failure: BaseException,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient([
        failure,
        FakeResponse(tool_calls=[
            {"id": "edit", "name": "apply_patch", "args": {
                "path": "README.md", "content": "Recovered.\n",
            }},
            {"id": "checks", "name": "run_checks", "args": {}},
        ]),
    ])

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert result.report["errors"] == []
    assert len(client.requests) == 2
    assert client.requests[0] == client.requests[1]
    assert result.report["budgets"]["usage"]["tool_calls"] == 2
    assert "raw-" not in str(client.requests)


@pytest.mark.parametrize(
    ("status", "expected_code"),
    [(400, "provider_invalid_request"), (401, "provider_authentication_failed"),
     (403, "provider_permission_denied"), (404, "provider_model_not_found")],
)
def test_permanent_provider_failures_stop_without_a_second_request(
    tmp_path: Path, status: int, expected_code: str,
) -> None:
    repository = configured_repository(tmp_path)
    cause = ClientError(status, {"error": {"message": "raw-provider-body"}})
    wrapped = RuntimeError("raw-sdk-wrapper")
    wrapped.__cause__ = cause
    client = FakeGeminiClient([wrapped, FakeResponse(content="must not execute")])

    result = run_gemini(repository, client)

    assert len(client.requests) == 1
    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.publication == {"intent": "none", "reason": "failed"}
    assert result.report["errors"][0]["code"] == expected_code
    assert "raw-" not in json.dumps(result.report)
    assert "raw-" not in str(client.requests)


def test_gemini_tool_calls_drive_only_the_controlled_patch_flow(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {"id": "list", "name": "list_files", "args": {}},
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "read",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    },
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Hello from Gemini.\n",
                        },
                    },
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == "Hello from Gemini.\n"
    assert result.report["model"] == {
        "provider": "google_gemini_developer_api",
        "name": "gemini-3.5-flash-lite",
    }
    assert [tool["function"]["name"] for tool in client.bound_tools] == [
        "list_files",
        "search_code",
        "read_file",
        "apply_patch",
        "inspect_diff",
        "run_checks",
    ]
    assert all(tool["type"] == "function" for tool in client.bound_tools)
    declarations = {
        tool["function"]["name"]: tool["function"] for tool in client.bound_tools
    }
    for declaration in declarations.values():
        assert "Example arguments:" in declaration["description"]
        assert declaration["parameters"]["additionalProperties"] is False
        for name, parameter in declaration["parameters"]["properties"].items():
            assert parameter["type"] == "string"
            assert parameter["description"]
            if name == "path":
                assert "Repository-relative" in parameter["description"]
                assert "POSIX" in parameter["description"]
    assert "case-sensitive literal substring" in declarations["search_code"]["description"]
    assert "1-based" in declarations["search_code"]["description"]
    for name in ("list_files", "search_code"):
        path_description = declarations[name]["parameters"]["properties"]["path"]["description"]
        assert "Defaults to '.'" in path_description
    patch_parameters = declarations["apply_patch"]["parameters"]
    assert patch_parameters["required"] == ["path", "content"]
    assert "complete final" in declarations["apply_patch"]["description"]
    assert "written verbatim" in patch_parameters["properties"]["content"]["description"]
    assert "not interpreted" in patch_parameters["properties"]["content"]["description"]
    for name in ("inspect_diff", "run_checks"):
        assert declarations[name]["parameters"]["properties"] == {}
        assert declarations[name]["parameters"]["required"] == []
    assert not any(
        forbidden in str(client.bound_tools).lower()
        for forbidden in (
            "code_execution",
            "google_search",
            "url_context",
            "computer_use",
            "remote_mcp",
        )
    )
    assert "AIzaSyDedicatedPatchLoopKey123456789" not in str(result.report)
    assert "AIzaSyDedicatedPatchLoopKey123456789" not in str(client.requests)


def test_legacy_tool_definition_can_still_be_bound_without_parameter_descriptions() -> None:
    definition = ToolDefinition("read_file", "Read a UTF-8 file.", ("path",), ())
    client = FakeGeminiClient([FakeResponse(content="Done.")])
    adapter = GeminiPatchModelAdapter(
        model_name="gemini-3.5-flash-lite",
        api_key="fixture-key",
        client=client,
    )

    adapter.next_turn(
        EvaluationTask("legacy-tool", "Read a file", "Read README.md."),
        (),
        (definition,),
    )

    assert definition.argument_descriptions == {}
    assert hash(definition) == hash(
        ToolDefinition("read_file", "Read a UTF-8 file.", ("path",), ())
    )
    assert client.bound_tools[0]["function"]["parameters"] == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }


def test_invalid_and_unknown_gemini_calls_are_validated_by_patchloop(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {"id": "bad", "name": "read_file", "args": "README.md"},
                    {"id": "unknown", "name": "shell", "args": {"command": "id"}},
                    {"id": "fields", "name": "read_file", "args": {"offset": 10}},
                ]
            ),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "corrected-read",
                        "name": "read_file",
                        "args": {"path": "README.md"},
                    },
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {"path": "README.md", "content": "Safe content.\n"},
                    },
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == "Safe content.\n"
    assert len(client.requests) == 2
    assert "invalid_arguments" in str(client.requests[1])
    assert "unknown_tool" in str(client.requests[1])
    feedback: dict[str, Any] = {}
    for message in client.requests[1]:
        if isinstance(message, ToolMessage):
            assert isinstance(message.content, str)
            feedback[message.tool_call_id] = json.loads(message.content)
    message = feedback["fields"]["error"]["message"]
    assert feedback["fields"]["error"]["code"] == "invalid_arguments"
    assert 'Missing required fields: ["path"]' in message
    assert 'Unexpected fields: ["offset"]' in message
    assert "Supply missing required fields and remove unexpected fields" in message
    assert result.report["budgets"]["usage"]["tool_calls"] == 6


@pytest.mark.parametrize(
    ("provider_error", "expected_code"),
    [
        (TimeoutError("AIzaSySecretTimeout123456789"), "provider_timeout"),
        (RateLimitError("AIzaSySecretRateLimit123456789"), "provider_rate_limited"),
        (RuntimeError("AIzaSySecretProvider123456789"), "provider_error"),
    ],
)
def test_provider_failures_are_stable_and_redacted(
    tmp_path: Path,
    provider_error: BaseException,
    expected_code: str,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient([provider_error] * 4)

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "failed"
    assert result.report["errors"] == [
        {
            "category": "model",
            "code": expected_code,
            "message": {
                "provider_timeout": "The Gemini request timed out.",
                "provider_rate_limited": "The Gemini request was rate limited.",
                "provider_error": "The Gemini provider could not complete the request.",
            }[expected_code],
        }
    ]
    assert len(client.requests) == (1 if expected_code == "provider_error" else 4)
    assert "AIzaSySecret" not in str(result.report)


def test_initial_provider_failure_retries_the_pending_turn(tmp_path: Path) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            TimeoutError("AIzaSySecretInitialProvider123456789"),
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Recovered initial request.\n",
                        },
                    },
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == (
        "Recovered initial request.\n"
    )
    assert result.report["verification"]["status"] == "checks_passed"
    assert len(client.requests) == 2
    assert len(client.requests[0]) == len(client.requests[1])
    assert "AIzaSySecret" not in str(result.report)
    assert "AIzaSySecret" not in str(client.requests)


def test_provider_failure_after_edit_retries_the_pending_turn(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Recovered after provider failure.\n",
                        },
                    },
                ]
            ),
            TimeoutError("AIzaSySecretTransientProvider123456789"),
            FakeResponse(
                tool_calls=[
                    {"id": "checks", "name": "run_checks", "args": {}},
                ]
            ),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "pr_created"
    assert repository.joinpath("README.md").read_text() == (
        "Recovered after provider failure.\n"
    )
    assert result.report["verification"]["status"] == "checks_passed"
    assert result.report["errors"] == []
    assert len(client.requests) == 3
    assert len(client.requests[1]) == len(client.requests[2])
    assert "AIzaSySecret" not in str(result.report)
    assert "AIzaSySecret" not in str(client.requests)


def test_repeated_provider_failure_after_edit_preserves_the_provider_error(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient(
        [
            FakeResponse(
                tool_calls=[
                    {
                        "id": "edit",
                        "name": "apply_patch",
                        "args": {
                            "path": "README.md",
                            "content": "Unverified provider recovery.\n",
                        },
                    },
                ]
            ),
            TimeoutError("AIzaSySecretFirstProvider123456789"),
            RateLimitError("AIzaSySecretSecondProvider123456789"),
            RateLimitError("raw-third"), RateLimitError("raw-last"),
        ]
    )

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "failed"
    assert result.patch is None
    assert result.report["errors"] == [
        {
            "category": "model",
            "code": "provider_rate_limited",
            "message": "The Gemini request was rate limited.",
        }
    ]
    assert repository.joinpath("README.md").read_text() == (
        "Unverified provider recovery.\n"
    )
    assert len(client.requests) == 5
    assert client.requests[1] == client.requests[2] == client.requests[3] == client.requests[4]
    assert "decision_after_edit" not in str(result.report)
    assert "AIzaSySecret" not in str(result.report)
    assert "AIzaSySecret" not in str(client.requests)


@pytest.mark.parametrize("text_length", [2_000, 4_001])
def test_completion_preserves_longer_text_up_to_4000_characters(text_length: int) -> None:
    raw_text = "完" * text_length
    adapter = GeminiPatchModelAdapter(
        model_name="gemini-3.5-flash-lite",
        api_key="test-key",
        client=FakeGeminiClient([FakeResponse(content=raw_text)]),
    )

    turn = adapter.next_turn(EvaluationTask(id="task", title="Task", body="Task body"), (), ())

    assert turn.completion is not None
    assert turn.completion.actionable_message == raw_text[:4_000]


def test_plain_text_is_a_stable_completion_without_persisting_raw_context(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    raw_text = "Done. " + ("x" * 2_000)
    client = FakeGeminiClient([FakeResponse(content=raw_text)])

    result = run_gemini(repository, client)

    assert result.terminal_outcome == "failed"
    assert result.report["errors"][0]["code"] == "empty_patch"
    assert raw_text not in str(result.report)


def test_dedicated_environment_secret_explicitly_selects_developer_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dedicated_key = "AIzaSyDedicatedPatchLoopKey123456789"
    monkeypatch.setenv("PATCHLOOP_GEMINI_API_KEY", dedicated_key)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyAmbientGoogleKey123456789")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyAmbientGeminiKey123456789")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    captured: dict[str, Any] = {}

    def client_factory(**kwargs: object) -> FakeGeminiClient:
        captured.update(kwargs)
        return FakeGeminiClient([])

    adapter = GeminiPatchModelAdapter.from_environment(
        "gemini-3.5-flash-lite", client_factory=client_factory
    )

    assert adapter.model_name == "gemini-3.5-flash-lite"
    assert captured == {
        "model": "gemini-3.5-flash-lite",
        "api_key": dedicated_key,
        "vertexai": False,
        "retries": 0,
        "request_timeout": 60.0,
    }


def test_ambient_google_credentials_do_not_replace_the_dedicated_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PATCHLOOP_GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "AIzaSyAmbientGoogleKey123456789")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyAmbientGeminiKey123456789")

    with pytest.raises(ConfigError) as error:
        GeminiPatchModelAdapter.from_environment("gemini-3.5-flash-lite")

    assert error.value.code == "gemini_api_key_missing"
    assert "AIzaSyAmbient" not in error.value.message


def test_configured_model_must_match_the_model_that_will_execute(
    tmp_path: Path,
) -> None:
    repository = configured_repository(tmp_path)
    client = FakeGeminiClient([FakeResponse(content="This must not execute.")])

    with pytest.raises(ConfigError) as error:
        run_gemini(repository, client, model_name="different-model")

    assert error.value.code == "model_config_mismatch"
    assert client.requests == []


def run_gemini(
    repository: Path,
    client: FakeGeminiClient,
    *,
    model_name: str = "gemini-3.5-flash-lite",
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] = lambda: 0.0,
    wall_clock: Callable[[], float] = lambda: 1791936000.0,
):
    retry_clock = RetryClock()
    return run(
        RunRequest(
            task=TaskSnapshot("issue-108", "Update greeting", "Change the README."),
            repository=repository,
            config=load_repository_config(repository / ".patchloop.yml"),
            adapters=RunAdapters(
                clock=clock or retry_clock,
                sleep=sleep or retry_clock.sleep,
                jitter=jitter,
                model=GeminiPatchModelAdapter(
                    model_name=model_name,
                    api_key="AIzaSyDedicatedPatchLoopKey123456789",
                    client=client,
                    wall_clock=wall_clock,
                ),
                verifier=DeterministicVerifierAdapter(
                    VerificationResult.passed(("check greeting",))
                ),
            ),
        )
    )


def configured_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README.md").write_text("Hello, world!\n")
    (repository / ".patchloop.yml").write_text(
        """\
version: 1
model: gemini-3.5-flash-lite
verifier:
  image: fixture
  setup:
    - prepare fixture
  checks:
    - check greeting
  limits:
    timeout_seconds: 300
    memory_mb: 512
    pids: 64
    output_bytes: 65536
budgets:
  max_iterations: 3
  max_tool_calls: 60
  max_changed_files: 20
  max_diff_lines: 2000
  max_wall_time_minutes: 30
"""
    )
    return repository
