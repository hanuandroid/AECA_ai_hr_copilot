"""The registry is a closed, code-controlled allow-list of existing HRMS routes; the action package has
no direct business-table write path and no generic HTTP client."""

import re
from pathlib import Path
from typing import get_args

import pytest
from fastapi.routing import APIRoute

from app.main import app
from app.models.enums import Role
from app.services.ai.actions.models import ActionArguments, ActionName, ActionProposal
from app.services.ai.actions.prompts import build_action_message
from app.services.ai.actions.registry import ACTION_REGISTRY
from app.services.ai.authorization import Capability, grant_for
from tests.ai.authorization.test_matrix import EXPECTED as PHASE2_MATRIX

ACTIONS_PACKAGE = Path(__file__).resolve().parents[3] / "app" / "services" / "ai" / "actions"

# Written independently of registry.py: (method, route) of the existing endpoint each action must call.
EXPECTED_ROUTES = {
    ActionName.LEAVE_CREATE: ("POST", "/api/v1/leaves/requests"),
    ActionName.LEAVE_APPROVE: ("POST", "/api/v1/leaves/requests/{}/approve"),
    ActionName.LEAVE_REJECT: ("POST", "/api/v1/leaves/requests/{}/reject"),
    ActionName.TICKET_CREATE: ("POST", "/api/v1/tickets"),
    ActionName.TICKET_ASSIGN: ("POST", "/api/v1/tickets/{}/assign"),
    ActionName.TICKET_STATUS_UPDATE: ("POST", "/api/v1/tickets/{}/status"),
    ActionName.ANNOUNCEMENT_CREATE: ("POST", "/api/v1/announcements"),
    ActionName.PROJECT_ASSIGN: ("POST", "/api/v1/employees/{}/projects"),
    ActionName.PROJECT_CREATE: ("POST", "/api/v1/employees/projects/catalog"),
    ActionName.EMPLOYEE_DEACTIVATE: ("DELETE", "/api/v1/employees/{}"),
}


def _normalise(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_registry_is_exactly_the_supported_actions():
    assert set(ACTION_REGISTRY) == set(ActionName) == set(EXPECTED_ROUTES)


@pytest.mark.parametrize("name", list(ActionName))
def test_every_action_maps_to_capability_existing_route_schema_and_confirmation(name):
    spec = ACTION_REGISTRY[name]
    assert spec.capability == Capability(name.value)
    assert (spec.method, _normalise(spec.path)) == EXPECTED_ROUTES[name]
    app_routes = {(method, _normalise(route.path)) for route in app.routes if isinstance(route, APIRoute) for method in route.methods}
    assert (spec.method, _normalise(spec.path)) in app_routes, "registered route must be an existing business endpoint"
    assert spec.requires_confirmation is True
    assert spec.params_model.model_config.get("extra") == "forbid"
    assert "://" not in spec.path and spec.path.startswith("/api/v1/")


def test_registry_is_immutable():
    with pytest.raises(TypeError):
        ACTION_REGISTRY[ActionName.LEAVE_CREATE] = ACTION_REGISTRY[ActionName.EMPLOYEE_DEACTIVATE]  # type: ignore[index]


def test_model_output_schema_has_no_routing_or_authorization_fields():
    fields = set(ActionProposal.model_fields) | set(ActionArguments.model_fields)
    for forbidden in ("url", "method", "http_method", "path", "headers", "authorized", "role", "confirmed", "sql", "token"):
        assert forbidden not in fields
    assert ActionProposal.model_config.get("extra") == "forbid" and ActionArguments.model_config.get("extra") == "forbid"
    assert set(get_args(ActionProposal.model_fields["action"].annotation)) == {n.value for n in ActionName} | {"NONE"}


@pytest.mark.parametrize("role,column", [(Role.ADMIN, 0), (Role.MANAGER, 1), (Role.EMPLOYEE, 2)])
def test_offered_actions_follow_the_phase2_matrix(role, column):
    """No permission duplication: availability is derived from the central matrix only."""
    offered = {spec.name.value for spec in ACTION_REGISTRY.values() if grant_for(role, spec.capability) is not None}
    expected = {cap.value for cap, rules in PHASE2_MATRIX.items() if rules[column] is not None and cap.value in ActionName.__members__}
    assert offered == expected


def test_prompt_lists_only_allowed_actions():
    from datetime import date

    employee_actions = [spec for spec in ACTION_REGISTRY.values() if grant_for(Role.EMPLOYEE, spec.capability)]
    message = build_action_message("help", employee_actions, date(2026, 10, 1))
    assert "LEAVE_CREATE" in message and "TICKET_CREATE" in message
    for denied in ("LEAVE_APPROVE", "EMPLOYEE_DEACTIVATE", "PROJECT_CREATE", "ANNOUNCEMENT_CREATE", "TICKET_STATUS_UPDATE"):
        assert denied not in message


# --- no direct database mutation / no generic HTTP client ------------------------------------------

_WRITE_PATTERNS = re.compile(
    r"\.add\(|\.add_all\(|\.delete\(|(?<![\w.])update\(|(?<![\w.])insert\(|(?<![\w.])delete\(|(?<![\w.])text\(|\.merge\(|executemany"
)


def test_only_the_confirmation_repository_writes_and_only_ai_pending_actions():
    for path in ACTIONS_PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path.name == "confirmation.py":
            for match in re.finditer(r"\bupdate\(([^)]*)\)", source):
                assert match.group(1) == "AIPendingAction"
            assert "self._db.add(record)" in source and source.count(".add(") == 1
            assert re.search(r"from app\.models\.(?!ai_pending_action)", source) is None
        else:
            assert not _WRITE_PATTERNS.search(source), f"write-capable call in {path.name}"
            assert ".commit(" not in source, f"commit in {path.name}"


def test_http_is_only_used_by_the_registry_bound_client():
    for path in ACTIONS_PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path.name != "client.py":
            assert not re.search(r"^\s*(import|from)\s+(httpx|requests|urllib|aiohttp|http\.client)\b", source, re.MULTILINE), path.name
    client_source = (ACTIONS_PACKAGE / "client.py").read_text(encoding="utf-8")
    assert client_source.count("client.request(") == 1  # the single registry-bound transport
    assert "spec.method, path" in client_source


def test_no_privileged_identity_in_action_code():
    for path in ACTIONS_PACKAGE.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "create_access_token" not in source and "jwt_secret" not in source and "admin@" not in source
