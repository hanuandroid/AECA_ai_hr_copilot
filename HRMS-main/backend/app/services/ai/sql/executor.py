"""Read-only SQL execution (defence in depth, independent of the parser/validator).

- A separate sqlite3 connection opened with the URI `file:...?mode=ro` (the OS-level handle cannot
  write) and `PRAGMA query_only=ON`; the app's async read/write engine is never used.
- sqlite3 authorizer, built per query (least privilege): only SQLITE_SELECT, SQLITE_READ of the
  allow-listed columns of the tables this validated query uses, and SQLITE_FUNCTION of allow-listed
  functions are permitted. SQLite also issues table-level READs with an empty column name (e.g.
  COUNT(*) over a subquery, CTEs such as my_team); those are allowed only for the query's tables and
  for names that are not real database tables. Everything else (INSERT/UPDATE/DELETE, DDL, PRAGMA,
  ATTACH, recursive CTEs, other tables, forbidden columns, unknown functions) is SQLITE_DENY.
- A progress handler aborts queries that exceed AI_SQL_TIMEOUT_SECONDS.
- At most `max_rows` rows are fetched (one extra is read only to report truncation).
- Driver errors are logged by type only and surfaced as a generic SQL_EXECUTION_ERROR.
"""

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.engine import make_url

from app.db.base import Base
from app.services.ai.errors import SQLExecutionError
from app.services.ai.sql.schema import TABLES

logger = structlog.get_logger()

ALLOWED_SQLITE_FUNCTIONS = frozenset(
    {"count", "sum", "avg", "min", "max", "total", "abs", "round", "length", "lower", "upper", "trim", "ltrim",
     "rtrim", "substr", "substring", "coalesce", "ifnull", "nullif", "iif", "date", "time", "datetime", "julianday",
     "strftime", "instr", "replace", "group_concat", "like", "glob"}
)
REAL_TABLES = frozenset(name.lower() for name in Base.metadata.tables) | {"alembic_version"}


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    duration_ms: int


def sqlite_path_from_url(database_url: str) -> Path:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        raise SQLExecutionError(detail="unsupported database url")
    return Path(url.database).resolve()


def build_authorizer(allowed_tables: frozenset[str]):
    readable = {(table, column) for table in allowed_tables if table in TABLES for column in TABLES[table].allowed_columns}

    def _authorizer(action: int, arg1: str | None, arg2: str | None, _db: str | None, _trigger: str | None) -> int:
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = str(arg1 or "").lower(), str(arg2 or "").lower()
            if (table, column) in readable:
                return sqlite3.SQLITE_OK
            if column == "" and not table.startswith("sqlite_") and (table in allowed_tables or table not in REAL_TABLES):
                return sqlite3.SQLITE_OK  # table-level read (no data): query tables or CTEs only
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK if str(arg2).lower() in ALLOWED_SQLITE_FUNCTIONS else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    return _authorizer


class ReadOnlySQLExecutor:
    def __init__(self, db_path: Path, *, timeout_seconds: float, max_rows: int):
        self._db_path = db_path
        self._timeout = timeout_seconds
        self._max_rows = max_rows

    async def execute(self, sql: str, params: dict[str, Any], *, allowed_tables: frozenset[str]) -> QueryResult:
        return await asyncio.to_thread(self._execute_sync, sql, params, allowed_tables)

    def _execute_sync(self, sql: str, params: dict[str, Any], allowed_tables: frozenset[str]) -> QueryResult:
        started = time.monotonic()
        deadline = started + self._timeout
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"{self._db_path.as_uri()}?mode=ro", uri=True, timeout=self._timeout)
            connection.execute("PRAGMA query_only = ON")
            connection.set_authorizer(build_authorizer(frozenset(t.lower() for t in allowed_tables)))
            connection.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100)
            cursor = connection.execute(sql, params)
            columns = [description[0] for description in cursor.description or []]
            fetched = cursor.fetchmany(self._max_rows + 1)
        except sqlite3.Error as exc:
            logger.warning("ai.sql.execution_failed", error_type=type(exc).__name__)
            raise SQLExecutionError(detail=type(exc).__name__) from exc
        finally:
            if connection is not None:
                connection.close()
        truncated = len(fetched) > self._max_rows
        rows = [dict(zip(columns, row)) for row in fetched[: self._max_rows]]
        return QueryResult(columns=columns, rows=rows, truncated=truncated, duration_ms=int((time.monotonic() - started) * 1000))
