"""Retention cleanup and the stale-action report (production hardening Phase 2: B-14, B-11).

Security rules under test: dry run by default; applying requires an ACTIVE ADMIN operator from the DB; audit events are
never deleted; EXECUTING/UNKNOWN actions are never purged; the purge itself is audited (counts only)."""

import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.models.ai_audit_event import AIAuditEvent
from app.models.ai_conversation import AIConversation, AIConversationMessage
from app.models.ai_pending_action import AIPendingAction
from app.models.employee import Employee
from app.models.enums import EmployeeStatus
from app.services.ai.actions.reconciliation import actions_needing_review
from app.services.ai.retention import RetentionAuthorizationError, run_retention
from tests.support.data import ADMIN, EMPLOYEE, INACTIVE, MANAGER

NOW = datetime(2026, 10, 13, 12, 0, 0)
OLD = NOW - timedelta(days=400)


def _conversation(updated_at: datetime, messages: int = 2) -> AIConversation:
    conversation = AIConversation(public_id=str(uuid.uuid4()), employee_id=EMPLOYEE.id, title="t", status="ACTIVE",
                                  message_count=messages, created_at=updated_at, updated_at=updated_at)
    return conversation


def _action(status: str, *, expires_at: datetime, used_at: datetime | None = None) -> AIPendingAction:
    return AIPendingAction(token_hash=uuid.uuid4().hex + uuid.uuid4().hex, employee_id=EMPLOYEE.id, role="EMPLOYEE",
                           action="apply_leave", target_employee_id=EMPLOYEE.id, resource_id=None, payload_json="{}",
                           binding_hash="b" * 64, summary="s", status=status, result_code=None,
                           created_at=expires_at - timedelta(minutes=5), expires_at=expires_at, used_at=used_at)


def _audit(created_at: datetime) -> AIAuditEvent:
    return AIAuditEvent(event_id=uuid.uuid4().hex, request_id=uuid.uuid4().hex, sequence=1, employee_id=EMPLOYEE.id,
                        event_type="CHAT_REQUEST", status="STARTED", metadata_json="{}", created_at=created_at)


@pytest.fixture
async def seeded(db):
    old_conv, recent_conv = _conversation(NOW - timedelta(days=91)), _conversation(NOW - timedelta(days=89))
    db.add_all([old_conv, recent_conv])
    await db.flush()
    for conversation in (old_conv, recent_conv):
        for sequence in (1, 2):
            db.add(AIConversationMessage(conversation_id=conversation.id, sequence=sequence, role="USER", content="x",
                                         request_id=uuid.uuid4().hex, created_at=conversation.updated_at))
    actions = {
        "completed_old": _action("COMPLETED", expires_at=NOW - timedelta(days=40), used_at=NOW - timedelta(days=31)),
        "completed_recent": _action("COMPLETED", expires_at=NOW - timedelta(days=40), used_at=NOW - timedelta(days=29)),
        "expired_old": _action("EXPIRED", expires_at=NOW - timedelta(days=31)),
        "pending_never_confirmed_old": _action("PENDING", expires_at=NOW - timedelta(days=31)),
        "pending_live": _action("PENDING", expires_at=NOW + timedelta(minutes=5)),
        "executing_old": _action("EXECUTING", expires_at=OLD, used_at=OLD),
        "unknown_old": _action("UNKNOWN", expires_at=OLD, used_at=OLD),
    }
    db.add_all(actions.values())
    db.add_all([_audit(OLD), _audit(NOW - timedelta(days=1))])
    await db.commit()
    return {"old_conv": old_conv.id, "recent_conv": recent_conv.id, "actions": {k: v.id for k, v in actions.items()}}


async def _counts(db) -> dict[str, int]:
    db.expire_all()
    return {
        "conversations": (await db.execute(select(func.count(AIConversation.id)))).scalar_one(),
        "messages": (await db.execute(select(func.count(AIConversationMessage.id)))).scalar_one(),
        "actions": (await db.execute(select(func.count(AIPendingAction.id)))).scalar_one(),
        "audit": (await db.execute(select(func.count(AIAuditEvent.id)))).scalar_one(),
    }


async def test_dry_run_is_the_default_and_deletes_nothing(db, seeded):
    before = await _counts(db)
    report = await run_retention(db, now=NOW)
    assert report.applied is False
    assert (report.conversations, report.conversation_messages, report.pending_actions) == (1, 2, 3)
    assert report.audit_events_past_retention == 1
    assert report.pending_actions_needing_reconciliation == 2
    assert await _counts(db) == before


async def test_apply_deletes_only_records_past_retention(db, seeded):
    before = await _counts(db)
    report = await run_retention(db, apply=True, operator_employee_id=ADMIN.id, now=NOW)
    assert report.applied is True
    after = await _counts(db)
    assert after["conversations"] == before["conversations"] - 1
    assert after["messages"] == before["messages"] - 2
    assert after["actions"] == before["actions"] - 3

    remaining_conversations = set((await db.execute(select(AIConversation.id))).scalars())
    assert seeded["recent_conv"] in remaining_conversations and seeded["old_conv"] not in remaining_conversations
    orphans = (await db.execute(select(func.count(AIConversationMessage.id)).where(
        AIConversationMessage.conversation_id == seeded["old_conv"]))).scalar_one()
    assert orphans == 0

    remaining_actions = set((await db.execute(select(AIPendingAction.id))).scalars())
    ids = seeded["actions"]
    for kept in ("completed_recent", "pending_live", "executing_old", "unknown_old"):
        assert ids[kept] in remaining_actions, kept
    for purged in ("completed_old", "expired_old", "pending_never_confirmed_old"):
        assert ids[purged] not in remaining_actions, purged


async def test_audit_events_are_never_deleted_and_the_purge_is_audited(db, seeded):
    before = await _counts(db)
    await run_retention(db, apply=True, operator_employee_id=ADMIN.id, now=NOW)
    db.expire_all()
    old = (await db.execute(select(func.count(AIAuditEvent.id)).where(AIAuditEvent.created_at < NOW - timedelta(days=365)))).scalar_one()
    assert old == 1  # still there

    after = await _counts(db)
    purge_events = (await db.execute(select(AIAuditEvent).where(AIAuditEvent.event_type == "RETENTION_PURGE"))).scalars().all()
    assert after["audit"] == before["audit"] + len(purge_events)
    assert {e.employee_id for e in purge_events} == {ADMIN.id}
    metadata = {json.loads(e.metadata_json)["operation"]: json.loads(e.metadata_json)["row_count"] for e in purge_events}
    assert metadata == {"retention:conversations": 1, "retention:conversation_messages": 2, "retention:pending_actions": 3}
    assert all(set(json.loads(e.metadata_json)) <= {"operation", "row_count"} for e in purge_events)


async def test_dry_run_is_not_audited_as_a_purge(db, seeded):
    await run_retention(db, now=NOW)
    count = (await db.execute(select(func.count(AIAuditEvent.id)).where(AIAuditEvent.event_type == "RETENTION_PURGE"))).scalar_one()
    assert count == 0


@pytest.mark.parametrize("operator_id", [None, MANAGER.id, EMPLOYEE.id, INACTIVE.id, 999_999])
async def test_apply_requires_an_active_admin_operator(db, seeded, operator_id):
    before = await _counts(db)
    with pytest.raises(RetentionAuthorizationError):
        await run_retention(db, apply=True, operator_employee_id=operator_id, now=NOW)
    assert await _counts(db) == before


async def test_deactivated_admin_cannot_apply(db, seeded):
    await db.execute(update(Employee).where(Employee.id == ADMIN.id).values(status=EmployeeStatus.INACTIVE))
    await db.commit()
    before = await _counts(db)
    with pytest.raises(RetentionAuthorizationError):
        await run_retention(db, apply=True, operator_employee_id=ADMIN.id, now=NOW)
    assert await _counts(db) == before


async def test_stale_action_report_lists_unknown_and_stale_executing_only(db, seeded):
    fresh = _action("EXECUTING", expires_at=NOW + timedelta(minutes=5), used_at=NOW - timedelta(minutes=2))
    db.add(fresh)
    await db.commit()
    before = await _counts(db)

    items = await actions_needing_review(db, executing_threshold_minutes=15, now=NOW)
    ids = seeded["actions"]
    assert {item["confirmation_record_id"] for item in items} == {ids["executing_old"], ids["unknown_old"]}
    assert all(set(item) == {"confirmation_record_id", "action", "status", "employee_id", "target_employee_id",
                             "resource_id", "claimed_at", "result_code"} for item in items)  # no payload/summary/token hash
    assert await _counts(db) == before  # read-only


# --- AIS-17: repeatability, atomicity and a scheduler-friendly exit status ------------------------------------------

async def test_apply_is_idempotent(db, seeded):
    first = await run_retention(db, apply=True, operator_employee_id=ADMIN.id, now=NOW)
    after_first = await _counts(db)
    second = await run_retention(db, apply=True, operator_employee_id=ADMIN.id, now=NOW)
    assert (first.conversations, first.pending_actions) == (1, 3)
    assert (second.conversations, second.conversation_messages, second.pending_actions) == (0, 0, 0)
    counts = await _counts(db)
    assert {k: v for k, v in counts.items() if k != "audit"} == {k: v for k, v in after_first.items() if k != "audit"}


async def test_a_failure_part_way_through_deletes_nothing(db, seeded, monkeypatch):
    """The three deletes share one transaction: if the last one fails, the earlier ones are rolled back."""
    before = await _counts(db)
    original = db.execute

    async def failing_execute(statement, *args, **kwargs):
        table = getattr(getattr(statement, "table", None), "name", None)
        if getattr(statement, "is_delete", False) and table == AIPendingAction.__tablename__:
            raise RuntimeError("simulated storage failure")  # the 3rd delete, after conversations and messages
        return await original(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", failing_execute)
    with pytest.raises(RuntimeError):
        await run_retention(db, apply=True, operator_employee_id=ADMIN.id, now=NOW)
    monkeypatch.setattr(db, "execute", original)
    await db.rollback()  # what the operator script's session does when the exception propagates
    assert await _counts(db) == before


async def test_check_exit_code_signals_a_due_purge(db, seeded):
    from app.services.ai.retention import CHECK_DUE_EXIT_CODE, check_exit_code
    assert check_exit_code(await run_retention(db, now=NOW)) == CHECK_DUE_EXIT_CODE == 3
    await run_retention(db, apply=True, operator_employee_id=ADMIN.id, now=NOW)
    assert check_exit_code(await run_retention(db, now=NOW)) == 0  # only audit/EXECUTING/UNKNOWN remain: not "due"


def _script():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "scripts" / "retention_purge.py"
    spec = importlib.util.spec_from_file_location("retention_purge_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_check_flag_is_a_dry_run_and_cannot_apply(db, seeded, monkeypatch, capsys):
    script = _script()
    monkeypatch.setattr(script, "configure_logging", lambda: None)  # keep the test process's logging configuration
    before = await _counts(db)
    monkeypatch.setattr("sys.argv", ["retention_purge.py", "--check"])
    assert await script.main() in (0, 3)  # depends on the wall clock; the exit code itself is tested above
    assert await _counts(db) == before
    monkeypatch.setattr("sys.argv", ["retention_purge.py", "--check", "--apply", "--operator-employee-id", str(ADMIN.id)])
    with pytest.raises(SystemExit) as refused:
        await script.main()
    assert refused.value.code == 2 and await _counts(db) == before
