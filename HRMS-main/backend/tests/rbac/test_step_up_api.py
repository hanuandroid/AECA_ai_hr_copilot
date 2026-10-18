"""G-11: password step-up on high-impact business endpoints (services/step_up.py). Synthetic data and passwords only.

Modes: `log` (default; check and log, never block), `enforce` (block), `off`. Authorization and existing validation
always run first; a refused step-up changes nothing; the password is never logged, echoed or stored.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.core.config import settings
from app.services.step_up import STEP_UP_HEADER
from tests.rbac.test_route_permissions import CASES, ROLES, classify
from tests.support.data import EMPLOYEE, INACTIVE, OUTSIDER, PASSWORD
from tests.support.db import business_snapshot
from tests.support.http import error_code

WRONG = "Wrong-Synthetic-Pass-7"
TOO_LONG = "x" * 129
E = "/api/v1/employees"


@dataclass(frozen=True)
class Op:
    id: str
    method: str
    url: str
    json: dict[str, Any] | None = None
    user: str = "admin"
    ok_status: int = 200
    extra: dict[str, Any] = field(default_factory=dict)


NEW_EMPLOYEE = {"name": "Step Up Hire", "email": "step.up.hire@hrms-test.dev", "password": "Synthetic-New-Pass-1",
                "role": "ADMIN", "joining_date": "2026-10-01"}
PROTECTED = [
    Op("deactivate", "DELETE", f"{E}/{OUTSIDER.id}"),
    Op("reactivate", "PATCH", f"{E}/{INACTIVE.id}/reactivate"),
    Op("create", "POST", E, NEW_EMPLOYEE),
    Op("update-password", "PUT", f"{E}/{EMPLOYEE.id}", {"password": "Synthetic-Reset-Pass-2"}),
    Op("update-role", "PUT", f"{E}/{EMPLOYEE.id}", {"role": "MANAGER"}),
    Op("update-email", "PUT", f"{E}/{EMPLOYEE.id}", {"email": "changed.address@hrms-test.dev"}),
    Op("update-bank", "PUT", f"{E}/{EMPLOYEE.id}", {"bank_account_number": "000111222333"}),
    Op("update-pan", "PUT", f"{E}/{EMPLOYEE.id}", {"pan_number": "ABCDE1234Z"}),
    Op("announcement-org", "POST", "/api/v1/announcements",
       {"title": "Townhall", "body": "Townhall moved to 5 PM.", "audience": "ORG"}),
    Op("poll-org", "POST", "/api/v1/polls", {"question": "Lunch venue?", "options": ["A", "B"], "audience": "ORG"}),
]
IDS = [op.id for op in PROTECTED]


async def _call(client, auth, op: Op, password: str | None = None):
    headers = dict(await auth(op.user))
    if password is not None:
        headers[STEP_UP_HEADER] = password
    return await client.request(op.method, op.url, json=op.json, headers=headers)


@pytest.fixture
def mode(monkeypatch):
    def _set(value: str) -> None:
        monkeypatch.setattr(settings, "step_up_enforcement", value)
    return _set


def test_initial_rollout_mode_is_log():
    assert settings.step_up_enforcement == "log"


# --- enforce ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("password,status,code", [(None, 403, "STEP_UP_REQUIRED"), ("", 403, "STEP_UP_REQUIRED"),
                                                  (WRONG, 403, "STEP_UP_FAILED"), (TOO_LONG, 403, "STEP_UP_FAILED")])
@pytest.mark.parametrize("op", PROTECTED, ids=IDS)
async def test_enforce_refuses_without_the_right_password_and_changes_nothing(client, auth, mode, op, password, status,
                                                                             code):
    mode("enforce")
    before = business_snapshot()
    response = await _call(client, auth, op, password)
    assert response.status_code == status and error_code(response) == code, response.text
    assert business_snapshot() == before
    assert WRONG not in response.text and TOO_LONG not in response.text


@pytest.mark.parametrize("op", PROTECTED, ids=IDS)
async def test_enforce_accepts_the_users_own_password(client, auth, mode, op):
    mode("enforce")
    before = business_snapshot()
    response = await _call(client, auth, op, PASSWORD)
    assert response.status_code == op.ok_status, response.text
    assert business_snapshot() != before


@pytest.mark.parametrize("op", PROTECTED, ids=IDS)
async def test_enforce_throttles_before_hashing_and_changes_nothing(client, auth, mode, monkeypatch, op):
    mode("enforce")
    monkeypatch.setattr(settings, "auth_login_rate_limit_per_minute", 2)
    before = business_snapshot()
    for _ in range(2):
        assert (await _call(client, auth, op, WRONG)).status_code == 403
    throttled = await _call(client, auth, op, PASSWORD)
    assert throttled.status_code == 429 and error_code(throttled) == "STEP_UP_THROTTLED"
    assert business_snapshot() == before


# --- authorization and validation still come first ---------------------------------------------------------------


@pytest.mark.parametrize("user,op", [
    ("manager", Op("deactivate", "DELETE", f"{E}/{OUTSIDER.id}")),
    ("employee", Op("create", "POST", E, NEW_EMPLOYEE)),
    ("manager", Op("update-role", "PUT", f"{E}/{EMPLOYEE.id}", {"role": "ADMIN"})),
    ("manager", Op("announcement-org", "POST", "/api/v1/announcements",
                   {"title": "Townhall", "body": "Townhall moved to 5 PM.", "audience": "ORG"})),
    ("employee", Op("poll-org", "POST", "/api/v1/polls", {"question": "Which option?", "options": ["A", "B"], "audience": "ORG"})),
])
async def test_unauthorized_callers_get_the_same_answer_as_before(client, auth, mode, user, op):
    mode("log")
    baseline = await _call(client, auth, Op(op.id, op.method, op.url, op.json, user=user))
    mode("enforce")
    enforced = await _call(client, auth, Op(op.id, op.method, op.url, op.json, user=user))
    assert enforced.status_code == baseline.status_code == 403
    assert enforced.json() == baseline.json() and "STEP_UP" not in enforced.text


async def test_validation_errors_keep_their_precedence(client, auth, mode):
    mode("enforce")
    missing = await _call(client, auth, Op("x", "DELETE", f"{E}/999999"))
    assert missing.status_code == 404 and error_code(missing) == "EMPLOYEE_NOT_FOUND"
    already = await _call(client, auth, Op("x", "PATCH", f"{E}/{EMPLOYEE.id}/reactivate"))
    assert already.status_code == 400 and error_code(already) == "ALREADY_ACTIVE"


# --- what stays passwordless ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("op", [
    Op("update-name", "PUT", f"{E}/{EMPLOYEE.id}", {"name": "Employee Renamed", "phone": "9000000001"}),
    Op("resubmit-unchanged", "PUT", f"{E}/{EMPLOYEE.id}", {"email": EMPLOYEE.email.upper(), "role": "EMPLOYEE"}),
    Op("announcement-team", "POST", "/api/v1/announcements",
       {"title": "Team sync", "body": "Team sync moved to 3 PM.", "audience": "TEAM"}, user="manager"),
    Op("announcement-default-manager", "POST", "/api/v1/announcements",
       {"title": "Team sync", "body": "Team sync moved to 3 PM."}, user="manager"),
    Op("poll-team-admin", "POST", "/api/v1/polls", {"question": "Which option?", "options": ["A", "B"], "audience": "TEAM"}),
], ids=lambda op: op.id)
async def test_non_sensitive_and_team_operations_need_no_password(client, auth, mode, op):
    mode("enforce")
    response = await _call(client, auth, op)
    assert response.status_code == 200, response.text


# --- log and off modes ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("op", PROTECTED, ids=IDS)
async def test_log_mode_never_blocks_but_records_the_outcome(client, auth, mode, op, capfd):
    mode("log")
    capfd.readouterr()
    before = business_snapshot()
    response = await _call(client, auth, op, WRONG if op.id == "create" else None)
    assert response.status_code == op.ok_status, response.text  # today's behaviour is preserved
    assert business_snapshot() != before
    output = capfd.readouterr().out
    assert "auth.step_up.not_satisfied" in output and ("INVALID" if op.id == "create" else "MISSING") in output
    assert WRONG not in output


async def test_off_mode_skips_the_check(client, auth, mode, capfd):
    mode("off")
    capfd.readouterr()
    assert (await _call(client, auth, PROTECTED[0])).status_code == 200
    assert "auth.step_up" not in capfd.readouterr().out


# --- privacy ---------------------------------------------------------------------------------------------------------


async def test_passwords_never_reach_stdout_stderr_or_responses(client, auth, mode, capfd):
    capfd.readouterr()
    bodies = []
    for value, password in (("log", WRONG), ("enforce", WRONG), ("enforce", TOO_LONG), ("enforce", PASSWORD)):
        mode(value)
        for op in PROTECTED[:3] + PROTECTED[-2:]:
            bodies.append((await _call(client, auth, op, password)).text)
    captured = capfd.readouterr()
    for secret in (WRONG, TOO_LONG, PASSWORD):
        assert secret not in captured.out and secret not in captured.err
        assert all(secret not in body for body in bodies)


# --- route-permission matrix under enforcement -----------------------------------------------------------------------

STEP_UP_ROUTES = {("DELETE", f"{E}/{{employee_id}}"), ("PATCH", f"{E}/{{employee_id}}/reactivate"), ("POST", E),
                  ("PUT", f"{E}/{{employee_id}}"), ("POST", "/api/v1/announcements"), ("POST", "/api/v1/polls")}
MATRIX = [case for case in CASES if (case.method, case.path) in STEP_UP_ROUTES]


def test_every_protected_route_is_in_the_permission_matrix():
    assert {(case.method, case.path) for case in MATRIX} == STEP_UP_ROUTES


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("case", MATRIX, ids=lambda c: f"{c.method} {c.path}")
async def test_matrix_holds_under_enforcement_with_the_password(client, auth, mode, case, role):
    """With enforcement on and the caller's own password supplied, every protected route gives exactly the matrix
    outcome of tests/rbac/test_route_permissions.py (authorization is unchanged; step-up adds nothing for them)."""
    mode("enforce")
    headers = {} if role == "anon" else {**await auth(role), STEP_UP_HEADER: PASSWORD}
    response = await client.request(case.method, case.url, headers=headers, json=case.json, data=case.data,
                                    files=case.files)
    assert classify(response.status_code) == case.expect[ROLES.index(role)], (case.method, case.url, role, response.text)
