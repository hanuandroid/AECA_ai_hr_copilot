"""Migration 0019: fresh upgrade, downgrade, re-upgrade, constraints, indexes and foreign keys."""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.db.base import Base
from app.services.ai.sql.schema import DENIED_TABLES, TABLES
from tests.conftest import BACKEND_DIR, TEST_DB_PATH

TABLES_P7 = ("ai_conversations", "ai_conversation_messages")


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    path = tmp_path / "migrate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return path, config


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_fresh_upgrade_downgrade_and_reupgrade(scratch_db):
    path, config = scratch_db
    command.upgrade(config, "head")
    assert set(TABLES_P7) <= _tables(path)
    command.downgrade(config, "0018_ai_pending_actions")
    tables = _tables(path)
    assert not (set(TABLES_P7) & tables) and "ai_pending_actions" in tables
    command.upgrade(config, "head")
    assert set(TABLES_P7) <= _tables(path)


def _connect():
    connection = sqlite3.connect(TEST_DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _conversation(connection, public_id="00000000-0000-4000-8000-000000000001", status="ACTIVE", owner=3) -> int:
    cursor = connection.execute(
        "INSERT INTO ai_conversations (public_id, employee_id, title, status, message_count, created_at, updated_at) "
        "VALUES (?, ?, 't', ?, 0, '2026-10-01', '2026-10-01')", (public_id, owner, status))
    return cursor.lastrowid


def _message(connection, conversation_id, sequence=1, role="USER"):
    connection.execute(
        "INSERT INTO ai_conversation_messages (conversation_id, sequence, role, content, request_id, created_at) "
        "VALUES (?, ?, ?, 'hi', 'r', '2026-10-01')", (conversation_id, sequence, role))


def test_indexes_exist():
    with _connect() as connection:
        indexes = {row[1] for row in connection.execute("SELECT * FROM sqlite_master WHERE type='index'")}
    assert {"ix_ai_conversations_public_id", "ix_ai_conversations_employee_id",
            "ix_ai_conversations_owner_status_updated"} <= indexes
    assert any("ai_conversation_messages" in name for name in indexes)  # UNIQUE(conversation_id, sequence) index


def test_message_requires_an_existing_conversation():
    with _connect() as connection, pytest.raises(sqlite3.IntegrityError):
        _message(connection, 999_999)


def test_conversation_requires_an_existing_owner():
    with _connect() as connection, pytest.raises(sqlite3.IntegrityError):
        _conversation(connection, owner=999_999)


def test_deleting_a_conversation_cascades_to_its_messages():
    with _connect() as connection:
        conversation_id = _conversation(connection)
        _message(connection, conversation_id, 1)
        _message(connection, conversation_id, 2, "ASSISTANT")
        connection.execute("DELETE FROM ai_conversations WHERE id = ?", (conversation_id,))
        count = connection.execute("SELECT COUNT(*) FROM ai_conversation_messages WHERE conversation_id = ?",
                                   (conversation_id,)).fetchone()[0]
    assert count == 0


@pytest.mark.parametrize("statement", [
    lambda c, cid: _message(c, cid, 1),  # duplicate sequence
    lambda c, cid: _message(c, cid, 0),  # sequence must be >= 1
    lambda c, cid: _message(c, cid, 5, "SYSTEM"),  # only USER / ASSISTANT
    lambda c, cid: _conversation(c, "00000000-0000-4000-8000-000000000001"),  # duplicate public id
    lambda c, cid: _conversation(c, "00000000-0000-4000-8000-000000000002", status="DELETED"),
])
def test_constraints(statement):
    with _connect() as connection:
        conversation_id = _conversation(connection)
        _message(connection, conversation_id, 1)
        with pytest.raises(sqlite3.IntegrityError):
            statement(connection, conversation_id)


def test_models_match_the_migration():
    for table in TABLES_P7:
        with _connect() as connection:
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        assert columns == set(Base.metadata.tables[table].columns.keys())


@pytest.mark.parametrize("table", TABLES_P7)
def test_sql_agent_can_never_read_conversations(table):
    assert table in DENIED_TABLES and table not in TABLES


def test_no_role_or_permission_columns_are_stored():
    for table in TABLES_P7:
        columns = set(Base.metadata.tables[table].columns.keys())
        assert not columns & {"role_name", "user_role", "permissions", "scope", "token", "jwt", "sql", "prompt"}
    assert "role" not in Base.metadata.tables["ai_conversations"].columns
