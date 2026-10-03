from pathlib import Path

from sqlalchemy import func, select, text

from app.core.config import settings
from app.db.session import engine
from app.models.announcement import Announcement
from app.models.employee import Employee
from tests.conftest import REAL_DEV_DB_PATH, TEST_DB_PATH, TEST_ROOT, reset_database

EXPECTED_HEAD = "0020_ai_audit_events"  # Phase 3: 0017 policy_chunks; Phase 5: 0018 ai_pending_actions; Phase 7: 0019 conversations; Phase 8: 0020 audit (AI-owned)
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
    # The checkout has no dev DB; the test run must not create one.
    assert not REAL_DEV_DB_PATH.exists()
