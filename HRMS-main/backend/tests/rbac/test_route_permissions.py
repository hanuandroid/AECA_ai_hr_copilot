"""Route-permission regression suite (RBAC remediation step 1, docs/production-hardening/rbac-remediation-plan.md).

Every API route x {anonymous, EMPLOYEE, MANAGER, ADMIN} with an explicit expected outcome class. Each (route, role)
pair runs on a fresh throwaway database. Requests use valid bodies so role checks inside handlers are exercised, and
path ids deliberately target resources OUTSIDE the caller's own scope (another employee outside the manager's team,
that employee's pending leave) so a regression into IDOR shows up as a changed class.

This file encodes the CURRENT approved behaviour. Each remediation step that intentionally changes a permission edits
the expectation here (the diff is the review record). `test_every_route_is_classified` fails when a route is added
without an explicit expectation: new routes are never silently unclassified.

Outcome classes: ok (2xx) - deny (403) - hidden (404, identical to an unknown id) - auth (401) - rejected (400/409/422:
past authorization, refused by validation or business rules).
"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.routing import APIRoute

from app.main import app
from tests.support.data import (
    EMPLOYEE,
    EMPLOYEE_TICKET_ID,
    INACTIVE,
    OUTSIDER,
    OUTSIDER_PENDING_LEAVE_ID,
    PASSWORD,
    PROJECT_ID,
)

OK, DENY, HIDDEN, AUTH, REJECTED = "ok", "deny", "hidden", "auth", "rejected"
ROLES = ("anon", "employee", "manager", "admin")
OTHER = OUTSIDER.id  # an employee outside the manager's team
UNKNOWN_UUID = "00000000-0000-4000-8000-000000000000"
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def classify(status: int) -> str:
    if status == 401:
        return AUTH
    if status == 403:
        return DENY
    if status == 404:
        return HIDDEN
    if 200 <= status < 300:
        return OK
    if status in (400, 409, 422):
        return REJECTED
    return str(status)


@dataclass(frozen=True)
class Case:
    method: str
    path: str  # route template, exactly as registered
    url: str  # concrete URL; "{poll}" is filled by the poll setup
    expect: tuple[str, str, str, str]  # anon, employee, manager, admin
    json: Any = None
    data: dict | None = None
    files: dict | None = None
    setup: str | None = None
    note: str = ""
    extra: dict = field(default_factory=dict)


def _doc_upload(title: str, filename: str = "note.txt", content: bytes = b"hello", mime: str = "text/plain") -> dict:
    return {"data": {"title": title, "document_type": "OTHER"}, "files": {"file": (filename, content, mime)}}


def _payslip_pdf() -> bytes:
    from io import BytesIO

    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, "Synthetic payslip")
    pdf.save()
    return buffer.getvalue()


E = "/api/v1/employees"
CASES: list[Case] = [
    # --- auth / health -------------------------------------------------------------------------------------------
    Case("POST", "/api/v1/auth/login", "/api/v1/auth/login", (OK, OK, OK, OK),
         json={"email": EMPLOYEE.email, "password": PASSWORD}),
    Case("POST", "/api/v1/auth/token", "/api/v1/auth/token", (OK, OK, OK, OK),
         data={"username": EMPLOYEE.email, "password": PASSWORD}),
    Case("POST", "/api/v1/auth/logout", "/api/v1/auth/logout", (AUTH, OK, OK, OK),
         note="B-17 follow-up: global sign-out of the caller only (token_version + 1)"),
    Case("POST", "/api/v1/auth/change-password", "/api/v1/auth/change-password", (AUTH, OK, OK, OK),
         json={"current_password": PASSWORD, "new_password": "a-brand-new-password"},
         note="Step 4: self-service password change for any authenticated active user (own account only)"),
    Case("GET", "/api/v1/health", "/api/v1/health", (OK, OK, OK, OK)),
    # --- employees: directory and self ---------------------------------------------------------------------------
    Case("GET", f"{E}/departments", f"{E}/departments", (AUTH, OK, OK, OK)),
    Case("GET", E, E, (AUTH, DENY, OK, OK),
         note="Decision 1 (step 2): EMPLOYEE denied (was ok); MANAGER sees self + direct reports only"),
    Case("GET", f"{E}/{{employee_id}}", f"{E}/{OTHER}", (AUTH, HIDDEN, HIDDEN, OK),
         note="Decision 1 (step 2): another employee is hidden from EMPLOYEE and from a non-managing MANAGER (was ok)"),
    Case("GET", f"{E}/skill-search", f"{E}/skill-search?skill=Python", (AUTH, DENY, OK, OK),
         note="Step 3: restricted org-wide skill search (approved fields only); not a directory"),
    Case("GET", f"{E}/me", f"{E}/me", (AUTH, OK, OK, OK)),
    Case("PUT", f"{E}/me", f"{E}/me", (AUTH, OK, OK, OK), json={"phone": "9876543210"}),
    Case("POST", f"{E}/me/profile-picture", f"{E}/me/profile-picture", (AUTH, OK, OK, OK),
         files={"file": ("me.png", PNG_1X1, "image/png")}),
    Case("GET", f"{E}/me/profile-picture", f"{E}/me/profile-picture", (AUTH, HIDDEN, HIDDEN, HIDDEN)),
    Case("GET", f"{E}/{{employee_id}}/profile-picture", f"{E}/{OTHER}/profile-picture", (AUTH, HIDDEN, HIDDEN, HIDDEN),
         note="no photo seeded; directory photo rules are covered by step 2 tests"),
    Case("GET", f"{E}/me/job-history", f"{E}/me/job-history", (AUTH, OK, OK, OK)),
    # --- employee documents ---------------------------------------------------------------------------------------
    Case("GET", f"{E}/me/documents", f"{E}/me/documents", (AUTH, OK, OK, OK)),
    Case("POST", f"{E}/me/documents", f"{E}/me/documents", (AUTH, OK, OK, OK), **_doc_upload("Own document")),
    Case("DELETE", f"{E}/me/documents/{{document_id}}", f"{E}/me/documents/999", (AUTH, HIDDEN, HIDDEN, HIDDEN)),
    Case("GET", f"{E}/me/documents/{{document_id}}/download", f"{E}/me/documents/999/download",
         (AUTH, HIDDEN, HIDDEN, HIDDEN)),
    Case("POST", f"{E}/{{employee_id}}/documents", f"{E}/{OTHER}/documents", (AUTH, DENY, DENY, OK),
         **_doc_upload("Contract for other")),
    Case("POST", f"{E}/{{employee_id}}/documents/payslip", f"{E}/{OTHER}/documents/payslip", (AUTH, DENY, DENY, OK),
         data={"title": "Payslip September", "period": "2026-09"}, setup="payslip"),
    # --- employee lifecycle (admin) -------------------------------------------------------------------------------
    Case("POST", E, E, (AUTH, DENY, DENY, OK),
         json={"name": "Synthetic Hire", "email": "synthetic.hire@hrms-test.dev", "password": "a-strong-password",
               "joining_date": "2026-10-01"}),
    Case("PUT", f"{E}/{{employee_id}}", f"{E}/{OTHER}", (AUTH, DENY, DENY, OK), json={"name": "Renamed Person"}),
    Case("DELETE", f"{E}/{{employee_id}}", f"{E}/{OTHER}", (AUTH, DENY, DENY, OK)),
    Case("PATCH", f"{E}/{{employee_id}}/reactivate", f"{E}/{INACTIVE.id}/reactivate", (AUTH, DENY, DENY, OK)),
    Case("PUT", f"{E}/{{employee_id}}/job-title", f"{E}/{OTHER}/job-title", (AUTH, DENY, DENY, OK),
         json={"designation": "Analyst"}),
    # --- projects ---------------------------------------------------------------------------------------------------
    Case("GET", f"{E}/projects/catalog", f"{E}/projects/catalog", (AUTH, DENY, OK, OK)),
    Case("POST", f"{E}/projects/catalog", f"{E}/projects/catalog", (AUTH, DENY, DENY, OK),
         json={"name": "Synthetic Project"}, note="step 6 (decision 5): catalogue creation is admin-only"),
    Case("PUT", f"{E}/projects/catalog/{{project_id}}/status", f"{E}/projects/catalog/{PROJECT_ID}/status",
         (AUTH, DENY, DENY, OK), json={"status": "ON_HOLD"},
         note="step 6 (X-3): manager only while they or a direct report are assigned; nobody is in the fixture"),
    Case("GET", f"{E}/{{employee_id}}/projects", f"{E}/{OTHER}/projects", (AUTH, DENY, DENY, OK)),
    Case("POST", f"{E}/{{employee_id}}/projects", f"{E}/{OTHER}/projects", (AUTH, DENY, DENY, OK),
         json={"project_id": PROJECT_ID}),
    Case("DELETE", f"{E}/{{employee_id}}/projects/{{project_id}}", f"{E}/{OTHER}/projects/{PROJECT_ID}",
         (AUTH, DENY, DENY, HIDDEN), note="admin: no such assignment"),
    # --- attendance / finance (self only) ---------------------------------------------------------------------------
    Case("GET", "/api/v1/attendance/me", "/api/v1/attendance/me", (AUTH, OK, OK, OK)),
    Case("GET", "/api/v1/attendance/today", "/api/v1/attendance/today", (AUTH, OK, OK, OK)),
    Case("POST", "/api/v1/attendance/clock-in", "/api/v1/attendance/clock-in", (AUTH, OK, OK, OK), json={}),
    Case("POST", "/api/v1/attendance/clock-out", "/api/v1/attendance/clock-out", (AUTH, REJECTED, REJECTED, REJECTED),
         json={}, note="not clocked in on a fresh DB"),
    Case("GET", "/api/v1/finance/payroll/me", "/api/v1/finance/payroll/me", (AUTH, OK, OK, OK)),
    Case("GET", "/api/v1/finance/statutory/me", "/api/v1/finance/statutory/me", (AUTH, OK, OK, OK)),
    Case("GET", "/api/v1/finance/profile/me", "/api/v1/finance/profile/me", (AUTH, OK, OK, OK)),
    # --- leaves -------------------------------------------------------------------------------------------------------
    Case("GET", "/api/v1/leaves/balances/me", "/api/v1/leaves/balances/me", (AUTH, OK, OK, OK)),
    Case("GET", "/api/v1/leaves/requests/me", "/api/v1/leaves/requests/me", (AUTH, OK, OK, OK)),
    Case("POST", "/api/v1/leaves/requests", "/api/v1/leaves/requests", (AUTH, OK, OK, OK),
         json={"leave_type": "SICK", "start_date": "2027-03-10", "end_date": "2027-03-10", "reason": "Synthetic"}),
    Case("GET", "/api/v1/leaves/requests/pending", "/api/v1/leaves/requests/pending", (AUTH, DENY, OK, OK)),
    Case("POST", "/api/v1/leaves/requests/{request_id}/approve",
         f"/api/v1/leaves/requests/{OUTSIDER_PENDING_LEAVE_ID}/approve", (AUTH, DENY, HIDDEN, OK),
         note="manager: outside team -> identical not-found (B-05)"),
    Case("POST", "/api/v1/leaves/requests/{request_id}/reject",
         f"/api/v1/leaves/requests/{OUTSIDER_PENDING_LEAVE_ID}/reject", (AUTH, DENY, HIDDEN, OK)),
    # --- announcements / polls / calendar / org -----------------------------------------------------------------------
    Case("GET", "/api/v1/announcements", "/api/v1/announcements", (AUTH, OK, OK, OK)),
    Case("POST", "/api/v1/announcements", "/api/v1/announcements", (AUTH, DENY, OK, OK),
         json={"title": "Synthetic news", "body": "Synthetic announcement body"},
         note="step 6 (decision 5): a manager's announcement is TEAM-scoped; asking for ORG is 403"),
    Case("GET", "/api/v1/polls", "/api/v1/polls", (AUTH, OK, OK, OK)),
    Case("POST", "/api/v1/polls", "/api/v1/polls", (AUTH, DENY, OK, OK),
         json={"question": "Synthetic question?", "options": ["a", "b"]}),
    Case("POST", "/api/v1/polls/{poll_id}/vote", "/api/v1/polls/{poll}/vote", (AUTH, OK, OK, OK),
         json={"option_index": 0}, setup="poll"),
    Case("GET", "/api/v1/polls/{poll_id}/results", "/api/v1/polls/{poll}/results", (AUTH, OK, OK, OK), setup="poll"),
    Case("GET", "/api/v1/calendar/team", "/api/v1/calendar/team?year=2026&month=10", (AUTH, OK, OK, OK),
         note="S-06: rows scoped (ADMIN all active; MANAGER self + active direct reports; EMPLOYEE self); tests/rbac/test_team_calendar_scope.py"),
    Case("GET", "/api/v1/calendar/holidays", "/api/v1/calendar/holidays", (AUTH, OK, OK, OK)),
    Case("GET", "/api/v1/calendar/birthdays", "/api/v1/calendar/birthdays", (AUTH, OK, OK, OK),
         note="S-08: active employees only; name, team, next date (no year); tests/rbac/test_self_service_scope.py"),
    Case("GET", "/api/v1/org/tree", "/api/v1/org/tree", (AUTH, OK, OK, OK)),
    # --- tickets ------------------------------------------------------------------------------------------------------
    Case("GET", "/api/v1/tickets", "/api/v1/tickets", (AUTH, OK, OK, OK)),
    Case("POST", "/api/v1/tickets", "/api/v1/tickets", (AUTH, OK, OK, OK),
         json={"title": "Synthetic issue", "description": "Synthetic description", "category": "IT", "priority": "LOW"}),
    Case("POST", "/api/v1/tickets/{ticket_id}/assign", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/assign",
         (AUTH, DENY, OK, OK), json={"assignee_id": EMPLOYEE.id}),
    Case("POST", "/api/v1/tickets/{ticket_id}/status", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/status",
         (AUTH, DENY, OK, OK), json={"status": "IN_PROGRESS"},
         note="A-07 fixed (step 5, decision 4): the EMPLOYEE owner may not change status; assignees may"),
    Case("POST", "/api/v1/tickets/{ticket_id}/onboarding-tasks", f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/onboarding-tasks",
         (AUTH, DENY, REJECTED, REJECTED), json={"task_name": "Synthetic task"}, note="IT ticket, not ONBOARDING"),
    Case("POST", "/api/v1/tickets/{ticket_id}/onboarding-tasks/{task_id}/complete",
         f"/api/v1/tickets/{EMPLOYEE_TICKET_ID}/onboarding-tasks/999/complete", (AUTH, HIDDEN, HIDDEN, HIDDEN)),
    # --- HR policies --------------------------------------------------------------------------------------------------
    Case("GET", "/api/v1/hr-policies", "/api/v1/hr-policies", (AUTH, OK, OK, OK)),
    Case("POST", "/api/v1/hr-policies/upload", "/api/v1/hr-policies/upload", (AUTH, DENY, OK, OK),
         data={"title": "Synthetic Policy", "category": "TEST"},
         files={"file": ("synthetic.md", b"# Synthetic\n\nText.", "text/markdown")},
         note="MANAGER upload is a SUBMITTED draft (ADMIN-only publication)"),
    Case("POST", "/api/v1/hr-policies/{policy_id}/publish", "/api/v1/hr-policies/1/publish", (AUTH, DENY, DENY, REJECTED),
         note="policy 1 is already PUBLISHED"),
    Case("POST", "/api/v1/hr-policies/{policy_id}/reject", "/api/v1/hr-policies/1/reject", (AUTH, DENY, DENY, REJECTED)),
    Case("GET", "/api/v1/hr-policies/{policy_id}/events", "/api/v1/hr-policies/1/events", (AUTH, DENY, DENY, OK)),
    Case("GET", "/api/v1/hr-policies/{policy_id}/download", "/api/v1/hr-policies/1/download", (AUTH, OK, OK, OK)),
    # --- AI (authorization of AI behaviour itself: tests/ai/**) ---------------------------------------------------------
    Case("POST", "/api/v1/chat", "/api/v1/chat", (AUTH, REJECTED, REJECTED, REJECTED), json={}),
    Case("POST", "/api/v1/chat/policy", "/api/v1/chat/policy", (AUTH, REJECTED, REJECTED, REJECTED), json={}),
    Case("POST", "/api/v1/chat/sql", "/api/v1/chat/sql", (AUTH, REJECTED, REJECTED, REJECTED), json={}),
    Case("POST", "/api/v1/chat/actions", "/api/v1/chat/actions", (AUTH, REJECTED, REJECTED, REJECTED), json={}),
    Case("POST", "/api/v1/chat/actions/confirm", "/api/v1/chat/actions/confirm", (AUTH, REJECTED, REJECTED, REJECTED),
         json={}),
    Case("GET", "/api/v1/chat/permissions", "/api/v1/chat/permissions", (AUTH, OK, OK, OK)),
    Case("GET", "/api/v1/chat/conversations", "/api/v1/chat/conversations", (AUTH, OK, OK, OK)),
    Case("GET", "/api/v1/chat/conversations/{conversation_id}", f"/api/v1/chat/conversations/{UNKNOWN_UUID}",
         (AUTH, HIDDEN, HIDDEN, HIDDEN)),
    Case("DELETE", "/api/v1/chat/conversations/{conversation_id}", f"/api/v1/chat/conversations/{UNKNOWN_UUID}",
         (AUTH, HIDDEN, HIDDEN, HIDDEN)),
    Case("POST", "/api/v1/chat/sessions", "/api/v1/chat/sessions", (AUTH, REJECTED, REJECTED, REJECTED), json={}),
    Case("POST", "/api/v1/chat/sessions/{session_id}/messages", "/api/v1/chat/sessions/1/messages",
         (AUTH, REJECTED, REJECTED, REJECTED), json={}),
    Case("GET", "/api/v1/ai/audit", "/api/v1/ai/audit", (AUTH, OK, OK, OK), note="own events for non-admins"),
    Case("GET", "/api/v1/ai/audit/metrics", "/api/v1/ai/audit/metrics", (AUTH, DENY, DENY, OK)),
]


def _registered_routes() -> set[tuple[str, str]]:
    return {(method, route.path) for route in app.routes if isinstance(route, APIRoute)
            and route.path.startswith("/api/v1") for method in route.methods - {"HEAD", "OPTIONS"}}


def test_every_route_is_classified():
    classified = {(case.method, case.path) for case in CASES}
    assert len(classified) == len(CASES), "duplicate route entries"
    assert _registered_routes() - classified == set(), "unclassified routes: add an explicit expectation"
    assert classified - _registered_routes() == set(), "expectations for routes that no longer exist"


async def _prepare(case: Case, client, auth) -> str:
    url = case.url
    if case.setup == "poll":
        response = await client.post("/api/v1/polls", json={"question": "Setup poll?", "options": ["x", "y"]},
                                     headers=await auth("admin"))
        url = url.replace("{poll}", str(response.json()["data"]["id"]))
    return url


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c.method} {c.path}")
async def test_route_permission(client, auth, case: Case, role: str):
    url = await _prepare(case, client, auth)
    headers = {} if role == "anon" else await auth(role)
    files = case.files
    if case.setup == "payslip":
        files = {"file": ("payslip.pdf", _payslip_pdf(), "application/pdf")}
    response = await client.request(case.method, url, headers=headers, json=case.json, data=case.data, files=files)
    expected = case.expect[ROLES.index(role)]
    assert classify(response.status_code) == expected, (
        f"{case.method} {url} as {role}: got {response.status_code}, expected {expected}. {case.note}"
    )
