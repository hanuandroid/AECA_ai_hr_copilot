"""The single authoritative AI authorization boundary.

AI authorization is stricter than existing HRMS authorization and must never be more
permissive (decision D7). Every AI capability (RAG, SQL, actions, orchestration) goes
through `authorize`/`require`/`run_authorized`; no other module may check roles directly.
"""

from app.services.ai.authorization.capabilities import Capability, ScopeLevel, TargetRule
from app.services.ai.authorization.context import AIUserContext, build_ai_user_context, get_ai_user_context
from app.services.ai.authorization.policy import AI_PERMISSION_MATRIX, capabilities_for, grant_for
from app.services.ai.authorization.scope import DataScope, TargetEmployee, hr_read_scope, policy_audiences, sql_read_scope
from app.services.ai.authorization.sensitive import SENSITIVE_COLUMNS, is_column_readable, is_sensitive_field
from app.services.ai.authorization.service import (
    GENERIC_DENIAL,
    AuthorizationResult,
    DenyReason,
    authorize,
    require,
    run_authorized,
)

__all__ = [
    "AI_PERMISSION_MATRIX",
    "AIUserContext",
    "AuthorizationResult",
    "Capability",
    "DataScope",
    "DenyReason",
    "GENERIC_DENIAL",
    "SENSITIVE_COLUMNS",
    "ScopeLevel",
    "TargetEmployee",
    "TargetRule",
    "authorize",
    "build_ai_user_context",
    "capabilities_for",
    "get_ai_user_context",
    "grant_for",
    "hr_read_scope",
    "is_column_readable",
    "is_sensitive_field",
    "policy_audiences",
    "require",
    "run_authorized",
    "sql_read_scope",
]
