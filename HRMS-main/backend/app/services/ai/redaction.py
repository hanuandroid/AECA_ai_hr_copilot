"""Redaction for text that is logged or persisted by the AI layer (never log raw user input)."""

import re

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # JWT-like tokens (header.payload.signature, base64url).
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer [REDACTED_TOKEN]"),
    # API keys such as sk-ant-..., sk-...
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[REDACTED_KEY]"),
    # Indian PAN: 5 letters, 4 digits, 1 letter.
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "[REDACTED_PAN]"),
    # IFSC: 4 letters, 0, 6 alphanumerics.
    (re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), "[REDACTED_IFSC]"),
    # Bank account / long numeric identifiers (9-18 digits).
    (re.compile(r"\b\d{9,18}\b"), "[REDACTED_NUMBER]"),
)


def contains_sensitive_value(text: str | None) -> bool:
    """True if the text contains a token/key/PAN/IFSC/account-number-like value."""
    if not text:
        return False
    return any(pattern.search(text) for pattern, _ in _REDACTIONS)


def redact(text: str | None, *, max_length: int = 2000) -> str:
    if not text:
        return ""
    result = text
    for pattern, replacement in _REDACTIONS:
        result = pattern.sub(replacement, result)
    if len(result) > max_length:
        result = result[:max_length] + "…[truncated]"
    return result
