"""Redaction for text that is logged or persisted by the AI layer (never log raw user input)."""

import re

# Named patterns, shared with the provider boundary (provider_policy.py).
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")  # header.payload.signature, base64url
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*")
API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")  # sk-ant-..., sk-...
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")  # Indian PAN: 5 letters, 4 digits, 1 letter
IFSC_RE = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")  # IFSC: 4 letters, 0, 6 alphanumerics
LONG_NUMBER_RE = re.compile(r"\b\d{9,18}\b")  # bank account / long numeric identifiers
# Provider boundary only (AIS-03); deliberately NOT in _REDACTIONS: redact() also masks the user's own SQL results and
# stored history, and changing those is decision G-09.
EMAIL_RE = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
AADHAAR_SPACED_RE = re.compile(r"(?<!\d)[2-9]\d{3}[ -]\d{4}[ -]\d{4}(?!\d)")  # unspaced 12 digits: LONG_NUMBER_RE
# Phone candidates: "+" forms, or 2-4 digit groups joined by ONE consistent separator (so a date such as 2026-10-18
# followed by " 98765 43210" stays two candidates); is_phone_number() decides.
PHONE_CANDIDATE_RE = re.compile(r"(?<![\w+])(?:\+\d[\d .()-]{6,18}\d|\(?\d{2,6}\)?([ .-])\d{2,5}(?:\1\d{2,5}){0,3})(?!\w)")


def is_phone_number(candidate: str) -> bool:
    """Plausible phone number: with a leading "+", 8-15 digits; otherwise an Indian mobile number (10 digits starting
    6-9, optionally with a 0 or 91 prefix). Dates, amounts, ranges and short references do not qualify."""
    digits = re.sub(r"\D", "", candidate)
    if candidate.startswith("+"):
        return 8 <= len(digits) <= 15
    if len(digits) == 11 and digits[0] == "0":
        digits = digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    return len(digits) == 10 and digits[0] in "6789"

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
