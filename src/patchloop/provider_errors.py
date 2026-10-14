from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

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
    details = getattr(error, "details", None)
    # google-genai keeps the complete JSON body in .details; wrappers may expose
    # the error object or its detail list instead. Do not parse exception text.
    if isinstance(details, Mapping):
        body = details.get("error", details)
        details = body.get("details") if isinstance(body, Mapping) else None
    if not isinstance(details, (list, tuple)):
        return False
    for detail in details[:32]:
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
