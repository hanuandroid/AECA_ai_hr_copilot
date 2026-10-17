"""Start-up migration revision guard and the supported migration paths (finding S-07, approved 2026-10-17).

Policy pinned here (docs/production-hardening/deployment-runbook.md §4, backup-restore.md):
- the API starts only when the database is exactly at the code's single Alembic head; the guard is read-only and never
  migrates, creates or stamps anything; every ambiguous state fails closed;
- production is forward-only; `alembic downgrade` is for disposable development databases only. On SQLite the
  development downgrade path is supported from head down to 0016; below 0016 it is unsupported (0016, 0012 and 0009
  cannot drop constraints on SQLite and are deliberately left unchanged).
All databases here are disposable SQLite files under tmp_path.
"""

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.revision_guard import MigrationRevisionError, assert_database_at_expected_revision, expected_revision
from tests.conftest import BACKEND_DIR

HEAD = "0024_employee_token_version"
SUPPORTED_DEV_DOWNGRADES = ["0023_policy_audience", "0022_content_audience", "0021_hr_policy_publication", "0020_ai_audit_events",
                            "0019_ai_conversations", "0018_ai_pending_actions", "0017_policy_chunks",
                            "0016_employee_documents_uploaded_by"]


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    path = tmp_path / "guard.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return path, config


def _engine(path: Path):
    return create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sql(path: Path, statement: str) -> list:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(statement).fetchall()
        connection.commit()
    return rows


async def _refusal(path: Path) -> str:
    engine = _engine(path)
    try:
        with pytest.raises(MigrationRevisionError) as caught:
            await assert_database_at_expected_revision(engine)
    finally:
        await engine.dispose()
    return str(caught.value)


def test_expected_revision_is_the_single_head():
    assert expected_revision() == HEAD


async def test_database_at_head_starts_and_is_not_modified(scratch):
    path, config = scratch
    command.upgrade(config, "head")
    before = _digest(path)
    engine = _engine(path)
    assert await assert_database_at_expected_revision(engine) == HEAD
    await engine.dispose()
    assert _digest(path) == before


async def test_older_database_is_refused_with_an_upgrade_hint(scratch):
    path, config = scratch
    command.upgrade(config, "0022_content_audience")
    before = _digest(path)
    message = await _refusal(path)
    assert "0022_content_audience" in message and HEAD in message and "older" in message and "upgrade head" in message
    assert _digest(path) == before  # nothing migrated or stamped
    assert _sql(path, "SELECT version_num FROM alembic_version") == [("0022_content_audience",)]


@pytest.mark.parametrize("future", ["0024_from_a_newer_release", "not-a-revision", "'; DROP TABLE employees; --"])
async def test_newer_or_unknown_database_is_refused_without_downgrade_advice(scratch, future):
    path, config = scratch
    command.upgrade(config, "head")
    _sql(path, f"UPDATE alembic_version SET version_num = '{future.replace(chr(39), chr(39) * 2)}'")
    before = _digest(path)
    message = await _refusal(path)
    assert "unknown to this code" in message and "Do not downgrade" in message and HEAD in message
    assert _digest(path) == before
    assert _sql(path, "SELECT count(*) FROM employees")  # table still there


async def test_missing_revision_table_fails_closed_and_creates_nothing(scratch):
    path, _ = scratch
    _sql(path, "CREATE TABLE something_else (id INTEGER)")
    message = await _refusal(path)
    assert "no readable Alembic revision" in message
    assert _sql(path, "SELECT name FROM sqlite_master WHERE name = 'alembic_version'") == []


async def test_missing_database_file_fails_closed_and_is_not_created(scratch):
    path, _ = scratch
    assert not path.exists()
    message = await _refusal(path)
    assert "does not exist" in message and not path.exists()


@pytest.mark.parametrize("rows", [[], ["0022_content_audience", HEAD]])
async def test_zero_or_several_recorded_revisions_fail_closed(scratch, rows):
    path, config = scratch
    command.upgrade(config, "head")
    _sql(path, "DELETE FROM alembic_version")
    for row in rows:
        _sql(path, f"INSERT INTO alembic_version (version_num) VALUES ('{row}')")
    message = await _refusal(path)
    assert f"records {len(rows)} Alembic revisions" in message


def test_multiple_code_heads_fail_closed(tmp_path):
    scripts = tmp_path / "alembic"
    shutil.copytree(BACKEND_DIR / "alembic", scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "9999_parallel_branch.py").write_text(
        'revision = "9999_parallel_branch"\ndown_revision = "0022_content_audience"\nbranch_labels = None\n'
        "depends_on = None\n\n\ndef upgrade():\n    pass\n\n\ndef downgrade():\n    pass\n", encoding="utf-8")
    with pytest.raises(MigrationRevisionError) as caught:
        expected_revision(scripts)
    assert "2 head revisions" in str(caught.value)


async def test_diagnostics_never_reveal_the_database_location_or_secrets(scratch, monkeypatch):
    path, config = scratch
    command.upgrade(config, "0021_hr_policy_publication")
    message = await _refusal(path)
    for leaked in (str(path), path.name, str(path.parent), "sqlite", "aiosqlite", "DATABASE_URL", "JWT"):
        assert leaked not in message, leaked


async def test_the_api_lifespan_refuses_to_start_on_a_mismatch(scratch, monkeypatch):
    from app import main

    path, config = scratch
    command.upgrade(config, "0022_content_audience")
    engine = _engine(path)
    monkeypatch.setattr(main, "engine", engine)
    with pytest.raises(MigrationRevisionError):
        async with main.app.router.lifespan_context(main.app):
            pytest.fail("the API must not start")
    command.upgrade(config, "head")  # migrations run without the application
    async with main.app.router.lifespan_context(main.app):
        pass  # now it starts
    await engine.dispose()


def test_guard_has_no_bypass_setting_and_runs_unconditionally():
    import inspect

    from app import main
    from app.core.config import settings

    knobs = {name for name in type(settings).model_fields if any(word in name.lower() for word in ("revision", "migration", "alembic"))}
    assert knobs == set()  # nothing in Settings can switch the guard off
    source = inspect.getsource(main.lifespan)
    call = "await assert_database_at_expected_revision(engine)"
    assert call in source
    line = next(line for line in source.splitlines() if call in line)
    assert line.startswith("    await")  # top level of the lifespan body, not inside an if/try


# --- supported and unsupported migration paths (development databases only) ----------------------------------------


def test_supported_dev_downgrades_and_reupgrade_from_head_to_0016(scratch):
    path, config = scratch
    command.upgrade(config, "head")
    for revision in SUPPORTED_DEV_DOWNGRADES:
        command.downgrade(config, revision)
        assert _sql(path, "SELECT version_num FROM alembic_version") == [(revision,)]
    command.upgrade(config, "head")
    assert _sql(path, "SELECT version_num FROM alembic_version") == [(HEAD,)]


def test_downgrade_below_0016_is_unsupported_on_sqlite(scratch):
    path, config = scratch
    command.upgrade(config, "head")
    command.downgrade(config, "0016_employee_documents_uploaded_by")
    with pytest.raises(NotImplementedError):
        command.downgrade(config, "0015_half_day_leave_support")
    # Documented hazard: the failed step is not atomic on SQLite; the revision stays 0016. Upgrading still works.
    assert _sql(path, "SELECT version_num FROM alembic_version") == [("0016_employee_documents_uploaded_by",)]
    command.upgrade(config, "head")
    assert _sql(path, "SELECT version_num FROM alembic_version") == [(HEAD,)]
