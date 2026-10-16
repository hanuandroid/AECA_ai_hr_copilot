"""Migration 0022 (announcement/poll audience): existing rows backfill to ORG, the CHECK constraint keeps audience and
team consistent, downgrade keeps every row, re-upgrade backfills again. Runs on SQLite, the supported database; other
engines are not verified."""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import BACKEND_DIR

TABLES = {"announcements": "INSERT INTO announcements (title, body, author_id, created_at) VALUES ('Old news', 'Body text', 1, '2026-01-01')",
          "polls": "INSERT INTO polls (question, options, created_by, created_at) VALUES ('Old poll?', '[\"a\", \"b\"]', 1, '2026-01-01')"}


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    path = tmp_path / "migrate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return path, config


def _columns(path, table) -> set[str]:
    with sqlite3.connect(path) as connection:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _rows(path, table) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT id, audience, team_manager_id FROM {table} ORDER BY id").fetchall()


def _execute(path, sql) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(sql)
        connection.commit()


def test_existing_rows_become_org_and_downgrade_keeps_them(scratch_db):
    path, config = scratch_db
    command.upgrade(config, "0021_hr_policy_publication")
    for table, insert in TABLES.items():
        assert "audience" not in _columns(path, table)
        _execute(path, insert)
    command.upgrade(config, "head")
    for table in TABLES:
        assert _rows(path, table) == [(1, "ORG", None)]
    command.downgrade(config, "0021_hr_policy_publication")
    for table in TABLES:
        assert not {"audience", "team_manager_id"} & _columns(path, table)
        with sqlite3.connect(path) as connection:
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
    command.upgrade(config, "head")
    for table in TABLES:
        assert _rows(path, table) == [(1, "ORG", None)]


@pytest.mark.parametrize("table", list(TABLES))
def test_new_rows_default_to_org_and_constraint_fails_closed(scratch_db, table):
    path, config = scratch_db
    command.upgrade(config, "head")
    _execute(path, TABLES[table])  # no audience given -> deterministic ORG
    assert _rows(path, table) == [(1, "ORG", None)]
    columns = "(title, body, author_id, created_at" if table == "announcements" else "(question, options, created_by, created_at"
    values = "('T', 'Body text', 1, '2026-01-01'" if table == "announcements" else "('Q?', '[]', 1, '2026-01-01'"
    for audience, team in (("TEAM", "NULL"), ("ORG", "2"), ("EVERYONE", "NULL"), ("team", "2"), ("", "NULL")):
        with pytest.raises(sqlite3.IntegrityError):
            _execute(path, f"INSERT INTO {table} {columns}, audience, team_manager_id) VALUES {values}, '{audience}', {team})")
    _execute(path, f"INSERT INTO {table} {columns}, audience, team_manager_id) VALUES {values}, 'TEAM', 2)")
    assert _rows(path, table)[-1][1:] == ("TEAM", 2)


def test_indexes_and_foreign_keys_exist(scratch_db):
    path, config = scratch_db
    command.upgrade(config, "head")
    with sqlite3.connect(path) as connection:
        for table in TABLES:
            indexes = {row[1] for row in connection.execute(f"PRAGMA index_list({table})")}
            assert {f"ix_{table}_audience", f"ix_{table}_team_manager_id"} <= indexes
            fks = {(row[3], row[2], row[4]) for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
            assert ("team_manager_id", "employees", "id") in fks
