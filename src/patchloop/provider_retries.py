from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import NotRequired, TypedDict

from patchloop.adapters import MIN_MODEL_REQUEST_SECONDS


MAX_REQUEST_RETRIES = 3
MAX_RUN_RETRIES = 6
RETRY_WAIT_SECONDS = 60.0
FALLBACK_DELAYS = (5.0, 10.0, 20.0)


class RetryStopReason(str, Enum):
    NON_RETRYABLE = "non_retryable"
    REQUEST_LIMIT = "request_retry_limit"
    RUN_LIMIT = "run_retry_limit"
    WAIT_LIMIT = "retry_wait_limit"
    RUN_DEADLINE = "run_deadline"
    PROVIDER_DELAY_WAIT_LIMIT = "provider_delay_wait_limit"
    PROVIDER_DELAY_RUN_DEADLINE = "provider_delay_run_deadline"


class ProviderRetryReport(TypedDict):
    attempts: int
    waiting_seconds: float
    recovered_requests: int
    stop_reason: NotRequired[str]


@dataclass
class ProviderRetries:
    """Run-local retry accounting; request execution is separate from waiting."""

    clock: Callable[[], float]
    sleep: Callable[[float], None]
    jitter: Callable[[], float]
    deadline: float
    attempts: int = 0
    waiting_seconds: float = 0.0
    recovered_requests: int = 0
    stop_reason: RetryStopReason | None = None

    def wait(self, request_retries: int, minimum_delay: float | None = None) -> bool:
        if request_retries >= MAX_REQUEST_RETRIES:
            self.stop_reason = RetryStopReason.REQUEST_LIMIT
            return False
        if self.attempts >= MAX_RUN_RETRIES:
            self.stop_reason = RetryStopReason.RUN_LIMIT
            return False
        base = minimum_delay if minimum_delay is not None else FALLBACK_DELAYS[request_retries]
        remaining_wait = RETRY_WAIT_SECONDS - self.waiting_seconds
        remaining_run = self.deadline - self.clock()
        if remaining_run < MIN_MODEL_REQUEST_SECONDS:
            self.stop_reason = RetryStopReason.RUN_DEADLINE
            return False
        if base > remaining_wait or remaining_wait <= 0:
            self.stop_reason = (
                RetryStopReason.PROVIDER_DELAY_WAIT_LIMIT if minimum_delay is not None
                else RetryStopReason.WAIT_LIMIT
            )
            return False
        if base > remaining_run - MIN_MODEL_REQUEST_SECONDS:
            self.stop_reason = (
                RetryStopReason.PROVIDER_DELAY_RUN_DEADLINE if minimum_delay is not None
                else RetryStopReason.RUN_DEADLINE
            )
            return False
        delay = base + min(max(0.0, min(1.0, self.jitter())), remaining_wait - base)
        # Only waiting-budget jitter may be trimmed. Do not schedule a wait
        # which leaves no runtime for its additional request.
        if delay > remaining_run - MIN_MODEL_REQUEST_SECONDS:
            self.stop_reason = RetryStopReason.RUN_DEADLINE
            return False
        before = self.clock()
        if delay > self.deadline - before - MIN_MODEL_REQUEST_SECONDS:
            self.stop_reason = RetryStopReason.RUN_DEADLINE
            return False
        self.sleep(delay)
        after = self.clock()
        self.waiting_seconds += max(0.0, after - before)
        if self.waiting_seconds > RETRY_WAIT_SECONDS:
            self.stop_reason = RetryStopReason.WAIT_LIMIT
            return False
        if after >= self.deadline:
            self.stop_reason = RetryStopReason.RUN_DEADLINE
            return False
        return True

    def report(self) -> ProviderRetryReport:
        report: ProviderRetryReport = {
            "attempts": self.attempts,
            "waiting_seconds": self.waiting_seconds,
            "recovered_requests": self.recovered_requests,
        }
        if self.stop_reason is not None:
            report["stop_reason"] = self.stop_reason.value
        return report
