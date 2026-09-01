from __future__ import annotations

import re


_CREDENTIAL_PATTERNS = (
    re.compile(
        r"(?i)\bauthorization\s*:\s*(?:bearer|basic)\s+[^\s;,]+"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|"
        r"private[_-]?key|aws[_-]?secret[_-]?access[_-]?key)"
        r"\s*[:=]\s*[^\s;,]+"
    ),
)


def redact_text(value: str) -> str:
    """Remove common credential shapes from text crossing a report boundary."""
    redacted = value
    for pattern in _CREDENTIAL_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted
