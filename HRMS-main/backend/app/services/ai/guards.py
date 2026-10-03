"""Deterministic data-protection guards shared by every AI component.

These are independent of the existing endpoints' behaviour: even where an existing
endpoint over-exposes data (audit finding R1), the AI layer must not.
"""

import re
from typing import Any

from app.services.ai.authorization.sensitive import SENSITIVE_COLUMNS, is_sensitive_field
from app.services.ai.errors import AIPermissionDeniedError

# Single definition lives in authorization/sensitive.py; kept here as an alias for existing callers.
SENSITIVE_FIELDS: frozenset[str] = SENSITIVE_COLUMNS

# Existing backend routes the AI layer must never call.
# R1 (repository-audit.md): GET /employees/{id} returns bank/PAN/salary/DOB to any user.
# TODO(R1): remove the GET /employees/{id} entry only after the endpoint is role-gated (decision D4).
_FORBIDDEN_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GET", re.compile(r"^/api/v1/employees/\d+/?$")),
    ("PUT", re.compile(r"^/api/v1/employees/\d+/?$")),
    ("*", re.compile(r"^/api/v1/finance(/.*)?$")),
    ("*", re.compile(r"^/api/v1/auth(/.*)?$")),
    ("*", re.compile(r"^/api/v1/employees/(me|\d+)/documents(/.*)?$")),
    ("*", re.compile(r"^/api/v1/employees/(me|\d+)/profile-picture/?$")),
)


def is_route_forbidden(method: str, path: str) -> bool:
    method = method.upper()
    clean_path = path.split("?", 1)[0]
    return any(
        (forbidden_method in ("*", method)) and pattern.match(clean_path)
        for forbidden_method, pattern in _FORBIDDEN_ROUTES
    )


def assert_route_allowed(method: str, path: str) -> None:
    if is_route_forbidden(method, path):
        raise AIPermissionDeniedError()


def strip_sensitive_fields(value: Any) -> Any:
    """Recursively drop sensitive keys from API payloads before they reach an LLM or user."""
    if isinstance(value, dict):
        return {
            key: strip_sensitive_fields(item)
            for key, item in value.items()
            if not is_sensitive_field(str(key))
        }
    if isinstance(value, list):
        return [strip_sensitive_fields(item) for item in value]
    return value
