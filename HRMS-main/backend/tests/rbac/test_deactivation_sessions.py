"""Deactivation ends sessions for good; reactivation does not revive them (session-lifecycle phase, 2026-10-20).

Policy (proposed default in the phase brief; implemented with the existing B-17 mechanism, no migration):
1. Deactivation immediately invalidates existing sessions.
2. Every access token issued before the deactivation stays invalid after reactivation.
3. Reactivation allows a fresh sign-in; it does not restore old sessions.
4. Role changes keep their current behaviour: the role is re-read from the database on every request.
5. Inactive accounts cannot authenticate.
Mechanism: DELETE /employees/{id} and PATCH /employees/{id}/reactivate both increment `employees.token_version` (an
atomic SQL increment; for an ADMIN target inside the same guarded UPDATE as the last-active-admin check). The
reactivation increment also covers accounts that were deactivated before this change (their version was never bumped).
While inactive, the status check answers first (403 USER_INACTIVE); after reactivation an old token gets 401
TOKEN_REVOKED. Before this change, the old token worked again after reactivation (deep-workflow report, EMP-C).
"""

from sqlalchemy import select, update

from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from tests.support.data import ADMIN, EMPLOYEE, MANAGER, OUTSIDER, PASSWORD
from tests.support.http import error_code

E = "/api/v1/employees"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client, email: str, password: str = PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _token(client, email: str, password: str = PASSWORD) -> str:
    response = await _login(client, email, password)
    assert response.status_code == 200, response.text
    return response.json()["data"]["access_token"]


async def _version(db, employee_id: int) -> int:
    db.expire_all()
    return (await db.execute(select(Employee.token_version).where(Employee.id == employee_id))).scalar_one()


async def test_old_token_rejected_after_deactivation_and_still_after_reactivation(client, auth, db):
    admin = await auth(ADMIN.key)
    old = await _token(client, EMPLOYEE.email)
    assert (await client.get(f"{E}/me", headers=_bearer(old))).status_code == 200  # works before deactivation
    version_before = await _version(db, EMPLOYEE.id)

    assert (await client.delete(f"{E}/{EMPLOYEE.id}", headers=admin)).status_code == 200
    inactive = await client.get(f"{E}/me", headers=_bearer(old))
    assert (inactive.status_code, error_code(inactive)) == (403, "USER_INACTIVE")  # immediately
    assert (await _version(db, EMPLOYEE.id)) == version_before + 1
    refused = await _login(client, EMPLOYEE.email)
    assert (refused.status_code, error_code(refused)) == (401, "INVALID_CREDENTIALS")  # inactive cannot authenticate

    assert (await client.patch(f"{E}/{EMPLOYEE.id}/reactivate", headers=admin)).status_code == 200
    for path in (f"{E}/me", "/api/v1/tickets", "/api/v1/chat/permissions", "/api/v1/leaves/balances/me"):
        revived = await client.get(path, headers=_bearer(old))
        assert (revived.status_code, error_code(revived)) == (401, "TOKEN_REVOKED"), path  # stays invalid
    assert (await _version(db, EMPLOYEE.id)) == version_before + 2

    fresh = await _token(client, EMPLOYEE.email)  # reactivation allows a fresh sign-in
    me = await client.get(f"{E}/me", headers=_bearer(fresh))
    assert me.status_code == 200 and me.json()["data"]["role"] == "EMPLOYEE"
    assert (await client.get(E, headers=_bearer(fresh))).status_code == 403  # still only employee rights
    # Unrelated sessions are untouched.
    assert (await client.get(f"{E}/me", headers=admin)).status_code == 200
    assert (await client.get(f"{E}/me", headers=await auth(OUTSIDER.key))).status_code == 200


async def test_a_fresh_session_after_reactivation_follows_the_existing_revocation_rules(client, auth):
    admin = await auth(ADMIN.key)
    assert (await client.delete(f"{E}/{EMPLOYEE.id}", headers=admin)).status_code == 200
    assert (await client.patch(f"{E}/{EMPLOYEE.id}/reactivate", headers=admin)).status_code == 200
    fresh = await _token(client, EMPLOYEE.email)
    assert (await client.post("/api/v1/auth/logout", headers=_bearer(fresh))).status_code == 200  # sign out everywhere
    after = await client.get(f"{E}/me", headers=_bearer(fresh))
    assert (after.status_code, error_code(after)) == (401, "TOKEN_REVOKED")
    assert (await client.get(f"{E}/me", headers=_bearer(await _token(client, EMPLOYEE.email)))).status_code == 200


async def test_deactivating_another_admin_revokes_that_admins_sessions(client, auth, db):
    admin = await auth(ADMIN.key)
    created = await client.post(E, headers=admin, json={"name": "Second Admin", "email": "second.admin@hrms-test.dev",
                                                         "password": "second-admin-pass", "role": "ADMIN",
                                                         "joining_date": "2026-01-05"})
    assert created.status_code == 200
    second_id = created.json()["data"]["id"]
    old = await _token(client, "second.admin@hrms-test.dev", "second-admin-pass")
    assert (await client.delete(f"{E}/{second_id}", headers=admin)).status_code == 200  # guarded admin path
    assert (await client.patch(f"{E}/{second_id}/reactivate", headers=admin)).status_code == 200
    revived = await client.get(f"{E}/me", headers=_bearer(old))
    assert (revived.status_code, error_code(revived)) == (401, "TOKEN_REVOKED")
    assert (await client.get(f"{E}/me", headers=admin)).status_code == 200  # the acting admin keeps their session


async def test_account_deactivated_before_this_change_does_not_revive_on_reactivation(client, auth, db):
    old = await _token(client, EMPLOYEE.email)
    # Simulate a deactivation made by the previous code: status changed, token_version NOT incremented.
    await db.execute(update(Employee).where(Employee.id == EMPLOYEE.id).values(status=EmployeeStatus.INACTIVE))
    await db.commit()
    assert (await client.patch(f"{E}/{EMPLOYEE.id}/reactivate", headers=await auth(ADMIN.key))).status_code == 200
    revived = await client.get(f"{E}/me", headers=_bearer(old))
    assert (revived.status_code, error_code(revived)) == (401, "TOKEN_REVOKED")


async def test_refused_deactivation_or_reactivation_changes_nothing(client, auth, db):
    """Non-admins (including the employee themself) cannot deactivate or reactivate; no status or version change."""
    employee_token = await _token(client, EMPLOYEE.email)
    version = await _version(db, EMPLOYEE.id)
    outsider_version = await _version(db, OUTSIDER.id)
    attempts = [
        await client.delete(f"{E}/{EMPLOYEE.id}", headers=_bearer(employee_token)),  # self
        await client.patch(f"{E}/{EMPLOYEE.id}/reactivate", headers=_bearer(employee_token)),
        await client.delete(f"{E}/{OUTSIDER.id}", headers=await auth(MANAGER.key)),
        await client.patch(f"{E}/{OUTSIDER.id}/reactivate", headers=await auth(EMPLOYEE.key)),
    ]
    assert [response.status_code for response in attempts] == [403, 403, 403, 403]
    assert (await _version(db, EMPLOYEE.id), await _version(db, OUTSIDER.id)) == (version, outsider_version)
    db.expire_all()
    statuses = (await db.execute(select(Employee.status).where(Employee.id.in_([EMPLOYEE.id, OUTSIDER.id])))).scalars().all()
    assert set(statuses) == {EmployeeStatus.ACTIVE}
    assert (await client.get(f"{E}/me", headers=_bearer(employee_token))).status_code == 200  # session untouched
    # An inactive account cannot reactivate itself either: its token is refused before any handler runs.
    assert (await client.delete(f"{E}/{EMPLOYEE.id}", headers=await auth(ADMIN.key))).status_code == 200
    self_reactivate = await client.patch(f"{E}/{EMPLOYEE.id}/reactivate", headers=_bearer(employee_token))
    assert (self_reactivate.status_code, error_code(self_reactivate)) == (403, "USER_INACTIVE")
    db.expire_all()
    assert (await db.execute(select(Employee.status).where(Employee.id == EMPLOYEE.id))).scalar_one() == EmployeeStatus.INACTIVE


async def test_refused_deactivations_do_not_bump_versions(client, auth, db):
    """Self-deactivation (400) and an already-inactive target (400) are refused before any write."""
    admin = await auth(ADMIN.key)
    admin_version = await _version(db, ADMIN.id)
    own = await client.delete(f"{E}/{ADMIN.id}", headers=admin)
    assert (own.status_code, error_code(own)) == (400, "INVALID_OPERATION")
    assert (await _version(db, ADMIN.id)) == admin_version
    assert (await client.delete(f"{E}/{OUTSIDER.id}", headers=admin)).status_code == 200
    version = await _version(db, OUTSIDER.id)
    again = await client.delete(f"{E}/{OUTSIDER.id}", headers=admin)
    assert (again.status_code, error_code(again)) == (400, "ALREADY_INACTIVE")
    assert (await _version(db, OUTSIDER.id)) == version


async def test_role_changes_keep_the_current_per_request_policy(client, auth, db):
    """Unchanged behaviour (pinned): a role change does not revoke sessions; the role is re-read on every request,
    so a demotion takes effect immediately on existing tokens and the JWT role claim is never trusted."""
    manager_token = await _token(client, MANAGER.email)
    assert (await client.get(E, headers=_bearer(manager_token))).status_code == 200
    version = await _version(db, MANAGER.id)
    demoted = await client.put(f"{E}/{MANAGER.id}", headers=await auth(ADMIN.key), json={"role": "EMPLOYEE"})
    assert demoted.status_code == 200
    assert (await client.get(E, headers=_bearer(manager_token))).status_code == 403  # demotion applies at once
    me = await client.get(f"{E}/me", headers=_bearer(manager_token))
    assert me.status_code == 200 and me.json()["data"]["role"] == "EMPLOYEE"
    assert (await _version(db, MANAGER.id)) == version  # role changes do not touch the token version
