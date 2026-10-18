"""Redaction for text that is logged or persisted by the AI layer (never log raw user input)."""

import re

# Named patterns, shared with the provider boundary (provider_policy.py).
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")  # header.payload.signature, base64url
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*")
API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")  # sk-ant-..., sk-...
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")  # Indian PAN: 5 letters, 4 digits, 1 letter
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")  # IFSC: 4 letters, 0, 6 alphanumerics
LONG_NUMBER_RE = re.compile(r"\b\d{9,18}\b")  # bank account / long numeric identifiers

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (JWT_RE, "[REDACTED_TOKEN]"),
    (BEARER_RE, "Bearer [REDACTED_TOKEN]"),
    (API_KEY_RE, "[REDACTED_KEY]"),
    (PAN_RE, "[REDACTED_PAN]"),
    (IFSC_RE, "[REDACTED_IFSC]"),
    (LONG_NUMBER_RE, "[REDACTED_NUMBER]"),
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
