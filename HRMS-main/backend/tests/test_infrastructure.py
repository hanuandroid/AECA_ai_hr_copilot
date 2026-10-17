from pathlib import Path

from sqlalchemy import func, select, text

from app.core.config import settings
from app.db.session import engine
from app.models.announcement import Announcement
from app.models.employee import Employee
from tests.conftest import (
    DEV_DB_FINGERPRINT_AT_START,
    REAL_DEV_DB_PATH,
    TEST_DB_PATH,
    TEST_ROOT,
    dev_db_fingerprint,
    reset_database,
)

EXPECTED_HEAD = "0024_employee_token_version"  # B-17 (2026-10-17): 0024 employees.token_version; RBAC step 7 (2026-10-17): 0023 policy audience + reply citations; step 6: 0022 announcement/poll audience (business); Phase 3: 0017 policy_chunks; Phase 5: 0018 ai_pending_actions; Phase 7: 0019 conversations; Phase 8: 0020 audit (AI-owned); closure 2026-10-14: 0021 policy publication (business)
EXPECTED_TABLES = {
    "announcements", "attendance_logs", "departments", "employee_documents", "employee_projects",
    "employee_skills", "employees", "holidays", "hr_policies", "job_history", "leave_balances",
    "leave_requests", "onboarding_tasks", "payroll_records", "poll_responses", "polls", "projects",
    "skills", "tickets", "policy_chunks", "ai_pending_actions", "ai_conversations", "ai_conversation_messages",
    "ai_audit_events",
}


def test_database_is_isolated():
    db_file = Path(engine.url.database).resolve()
    assert db_file == TEST_DB_PATH.resolve()
    assert TEST_ROOT.resolve() in db_file.parents
    assert db_file != REAL_DEV_DB_PATH.resolve()
    assert settings.database_url == engine.url.render_as_string(hide_password=False)
    for upload_dir in (settings.policy_upload_dir, settings.profile_photo_upload_dir, settings.employee_document_upload_dir):
        assert TEST_ROOT.resolve() in Path(upload_dir).resolve().parents


async def test_migrations_run(db):
    head = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert head == EXPECTED_HEAD
    tables = {row[0] for row in (await db.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()}
    assert EXPECTED_TABLES <= tables


async def test_test_seed_isolated(db, template_db):
    assert (await db.execute(select(func.count(Employee.id)))).scalar_one() == 5
    db.add(Announcement(title="Leaked between tests?", body="Should be discarded", author_id=1))
    await db.commit()
    assert (await db.execute(select(func.count(Announcement.id)))).scalar_one() == 1
    await db.close()

    await reset_database(template_db)

    async with type(db)(bind=engine) as fresh:
        assert (await fresh.execute(select(func.count(Announcement.id)))).scalar_one() == 0


def test_real_dev_database_untouched():
    """The test run must neither create nor modify the developer's real database.

    Old assertion (until 2026-10-11): `not REAL_DEV_DB_PATH.exists()`, which only held on a fresh checkout and failed on
    every machine that followed the README (docker-compose creates and seeds storage/hrms.db). Intent kept, and made
    stricter for that case: absent at session start -> still absent; present -> byte-identical (sha256).
    Caveat: a dev server writing to the same file during the run would also change it.
    """
    assert dev_db_fingerprint() == DEV_DB_FINGERPRINT_AT_START, f"{REAL_DEV_DB_PATH} was created or modified by the test run"
