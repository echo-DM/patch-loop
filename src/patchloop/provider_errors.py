from __future__ import annotations

from collections.abc import Mapping
from datetime import timezone
from enum import Enum
from email.utils import parsedate_to_datetime
from itertools import islice
from math import isfinite
import re

import httpx
from langchain_core.exceptions import (
    ModelAuthenticationError,
    ModelConnectionError,
    ModelInvalidRequestError,
    ModelNotFoundError,
    ModelPermissionDeniedError,
    ModelRateLimitError,
    ModelTimeoutError,
)


class ProviderFailure(str, Enum):
    TIMEOUT = "provider_timeout"
    RATE_LIMITED = "provider_rate_limited"
    CONNECTION_FAILED = "provider_connection_failed"
    UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "provider_invalid_request"
    AUTHENTICATION_FAILED = "provider_authentication_failed"
    PERMISSION_DENIED = "provider_permission_denied"
    MODEL_NOT_FOUND = "provider_model_not_found"
    DAILY_QUOTA_EXHAUSTED = "provider_daily_quota_exhausted"
    UNKNOWN = "provider_error"

    @property
    def retryable(self) -> bool:
        return self in {
            self.TIMEOUT, self.RATE_LIMITED, self.CONNECTION_FAILED, self.UNAVAILABLE,
        }

    @property
    def message(self) -> str:
        return _MESSAGES[self]


_MESSAGES = {
    ProviderFailure.TIMEOUT: "The Gemini request timed out.",
    ProviderFailure.RATE_LIMITED: "The Gemini request was rate limited.",
    ProviderFailure.CONNECTION_FAILED: "The Gemini connection failed.",
    ProviderFailure.UNAVAILABLE: "The Gemini service is temporarily unavailable.",
    ProviderFailure.INVALID_REQUEST: "Gemini rejected the request. Check the model input and configuration.",
    ProviderFailure.AUTHENTICATION_FAILED: "Gemini authentication failed. Check PATCHLOOP_GEMINI_API_KEY.",
    ProviderFailure.PERMISSION_DENIED: "Gemini denied access. Check the API key's project permissions.",
    ProviderFailure.MODEL_NOT_FOUND: "Gemini could not find the model. Check the configured model identifier.",
    ProviderFailure.DAILY_QUOTA_EXHAUSTED: "Gemini's daily quota is exhausted. Check the project's quota in AI Studio and rerun after the daily allowance resets.",
    ProviderFailure.UNKNOWN: "The Gemini provider could not complete the request.",
}

_PERMANENT_STATUS_CODES = {
    400: ProviderFailure.INVALID_REQUEST,
    401: ProviderFailure.AUTHENTICATION_FAILED,
    403: ProviderFailure.PERMISSION_DENIED,
    404: ProviderFailure.MODEL_NOT_FOUND,
}


def normalize_provider_failure(error: BaseException) -> ProviderFailure:
    """Discard raw provider data and recognize failures in a bounded SDK chain."""
    chain = _exception_chain(error)
    for item in chain:
        status = _http_status(item)
        if status in _PERMANENT_STATUS_CODES:
            return _PERMANENT_STATUS_CODES[status]
        if status == 429:
            if any(_has_daily_quota_violation(cause) for cause in chain):
                return ProviderFailure.DAILY_QUOTA_EXHAUSTED
            return ProviderFailure.RATE_LIMITED
        if status == 408:
            return ProviderFailure.TIMEOUT
        if status in {500, 502, 503, 504}:
            return ProviderFailure.UNAVAILABLE
        if status is not None:
            return ProviderFailure.UNKNOWN
    for item in chain:
        for error_type, failure in _RECOGNIZED_EXCEPTION_TYPES:
            if isinstance(item, error_type):
                if failure is ProviderFailure.RATE_LIMITED and any(
                    _has_daily_quota_violation(cause) for cause in chain
                ):
                    return ProviderFailure.DAILY_QUOTA_EXHAUSTED
                return failure
    return ProviderFailure.UNKNOWN


_DAILY_QUOTA_IDS = frozenset({
    "GenerateRequestsPerDayPerProjectPerModel",
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    "GenerateContentInputTokensPerModelPerDay",
    "GenerateContentInputTokensPerModelPerDay-FreeTier",
})
_DAILY_QUOTA_METRICS = frozenset({
    "generativelanguage.googleapis.com/generate_requests_per_model_per_day",
})


def _has_daily_quota_violation(error: BaseException) -> bool:
    for detail in _structured_details(error):
        if not isinstance(detail, Mapping) or detail.get("@type") != "type.googleapis.com/google.rpc.QuotaFailure":
            continue
        violations = detail.get("violations")
        if not isinstance(violations, (list, tuple)):
            continue
        for violation in violations[:32]:
            if not isinstance(violation, Mapping):
                continue
            quota_id = violation.get("quotaId")
            metric = violation.get("quotaMetric")
            if _recognized_identifier(quota_id, _DAILY_QUOTA_IDS) or _recognized_identifier(metric, _DAILY_QUOTA_METRICS):
                return True
    return False


def _structured_details(error: BaseException) -> list[object] | tuple[object, ...]:
    details = getattr(error, "details", None)
    # google-genai keeps the complete JSON body in .details; wrappers may expose
    # the error object or its detail list instead. Do not parse exception text.
    if isinstance(details, Mapping):
        body = details.get("error", details)
        details = body.get("details") if isinstance(body, Mapping) else None
    if not isinstance(details, (list, tuple)):
        return ()
    return details[:32]


def normalize_provider_delay(error: BaseException, now: float) -> float | None:
    """Extract only a finite minimum delay; discard all provider source data."""
    delays: list[float] = []
    for item in _exception_chain(error):
        for detail in _structured_details(item):
            if isinstance(detail, Mapping) and detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
                raw = detail.get("retryDelay")
                if isinstance(raw, str) and len(raw) <= 128 and re.fullmatch(r"[0-9]+(?:\.[0-9]{1,9})?s", raw):
                    delay = float(raw[:-1])
                    if isfinite(delay):
                        delays.append(delay)
        response = getattr(item, "response", None)
        for headers in (getattr(item, "headers", None), getattr(response, "headers", None)):
            if not isinstance(headers, Mapping):
                continue
            # Direct lookups avoid scanning an unbounded header collection.
            for name in ("retry-after", "Retry-After", "RETRY-AFTER"):
                header_delay = _retry_after(headers.get(name), now)
                if header_delay is not None:
                    delays.append(header_delay)
            for name, value in islice(headers.items(), 32):
                if isinstance(name, str) and len(name) <= 128 and name.lower() == "retry-after":
                    header_delay = _retry_after(value, now)
                    if header_delay is not None:
                        delays.append(header_delay)
    return max(delays) if delays else None


_DAY_NAME = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_MONTH_NAME = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_TIME_OF_DAY = r"[0-9]{2}:[0-9]{2}:[0-9]{2}"
_HTTP_DATE = re.compile(
    rf"(?:{_DAY_NAME}, [0-9]{{2}} {_MONTH_NAME} [0-9]{{4}} {_TIME_OF_DAY} GMT"
    rf"|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [0-9]{{2}}-{_MONTH_NAME}-[0-9]{{2}} {_TIME_OF_DAY} GMT"
    rf"|{_DAY_NAME} {_MONTH_NAME} (?:[0-9]{{2}}| [0-9]) {_TIME_OF_DAY} [0-9]{{4}})"
)


def _retry_after(raw: object, now: float) -> float | None:
    if not isinstance(raw, str) or len(raw) > 128:
        return None
    value = raw.strip()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        seconds = float(value)
        return seconds if isfinite(seconds) else None
    if _HTTP_DATE.fullmatch(value) is None:
        return None
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            # HTTP's obsolete asctime form has an implicit GMT timezone.
            date = date.replace(tzinfo=timezone.utc)
        seconds = max(0.0, date.timestamp() - now)
        return seconds if isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _recognized_identifier(value: object, identifiers: frozenset[str]) -> bool:
    return isinstance(value, str) and len(value) <= 256 and value in identifiers


_RECOGNIZED_EXCEPTION_TYPES: tuple[tuple[type[BaseException], ProviderFailure], ...] = (
    (ModelAuthenticationError, ProviderFailure.AUTHENTICATION_FAILED),
    (ModelPermissionDeniedError, ProviderFailure.PERMISSION_DENIED),
    (ModelInvalidRequestError, ProviderFailure.INVALID_REQUEST),
    (ModelNotFoundError, ProviderFailure.MODEL_NOT_FOUND),
    (ModelRateLimitError, ProviderFailure.RATE_LIMITED),
    (ModelTimeoutError, ProviderFailure.TIMEOUT),
    (TimeoutError, ProviderFailure.TIMEOUT),
    (httpx.TimeoutException, ProviderFailure.TIMEOUT),
    (ModelConnectionError, ProviderFailure.CONNECTION_FAILED),
    (ConnectionError, ProviderFailure.CONNECTION_FAILED),
    (httpx.NetworkError, ProviderFailure.CONNECTION_FAILED),
    (httpx.RemoteProtocolError, ProviderFailure.CONNECTION_FAILED),
    (httpx.ProxyError, ProviderFailure.CONNECTION_FAILED),
)


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(chain) < 8 and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return tuple(chain)


def _http_status(error: BaseException) -> int | None:
    response = getattr(error, "response", None)
    for value in (
        getattr(error, "status_code", None),
        getattr(error, "code", None),
        getattr(response, "status_code", None),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and 400 <= value <= 599:
            return value
    return None
