"""Start-up migration revision guard (finding S-07, approved 2026-10-17).

The API refuses to start unless the database's Alembic revision equals the single head revision of the migrations
shipped with the running code. That stops two unsafe pairings: new code on an un-migrated (older) database, and old code
on a newer database - the second would ignore columns such as policy and content audiences and show restricted content.

Rules:
- Read-only: one SELECT of `alembic_version`. Never creates, upgrades, downgrades or stamps anything; a missing SQLite
  file is reported, not created.
- Fail closed: no revision table, zero or several recorded revisions, several code heads, an unknown revision or an
  unreachable database all refuse start-up.
- No bypass setting. Alembic commands do not import the application, so `alembic upgrade head` keeps working while the
  API is refused. Tests use the ASGI app without its lifespan and call this guard directly.
- The diagnostic names only the two revisions and the next step; never the database URL, paths or credentials.
Production is forward-only: fix a mismatch with `alembic upgrade head` (older database) or by restoring the matching
image and pre-upgrade backup (newer database), never with `alembic downgrade` (docs/production-hardening/
deployment-runbook.md §4).
"""

from pathlib import Path

import structlog
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger()

SCRIPT_LOCATION = Path(__file__).resolve().parents[2] / "alembic"
UPGRADE_HINT = "Run `alembic -c alembic.ini upgrade head` (a one-off container), then start the API again."
RESTORE_HINT = ("Do not downgrade. Start the application version that matches this database, or restore the matching "
                "pre-upgrade backup with the previous images (deployment-runbook.md §4).")


class MigrationRevisionError(RuntimeError):
    """The database schema revision does not match the running code; the API must not start."""


def _scripts(script_location: Path) -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(script_location))
    return ScriptDirectory.from_config(config)


def expected_revision(script_location: Path = SCRIPT_LOCATION) -> str:
    """The single head revision of the migrations shipped with this code."""
    heads = _scripts(script_location).get_heads()
    if len(heads) != 1:
        raise MigrationRevisionError(f"The migrations shipped with this code have {len(heads)} head revisions; exactly "
                                     "one is required. The API did not start.")
    return heads[0]


def _missing_sqlite_file(database_url: str) -> bool:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:") or url.database.startswith("file:"):
        return False
    return not Path(url.database).exists()


async def database_revisions(engine: AsyncEngine) -> list[str]:
    """Rows of `alembic_version` (read-only). Raises MigrationRevisionError if they cannot be read."""
    if _missing_sqlite_file(engine.url.render_as_string(hide_password=False)):
        raise MigrationRevisionError("The database file does not exist yet. " + UPGRADE_HINT)
    try:
        async with engine.connect() as connection:
            rows = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    except SQLAlchemyError:
        raise MigrationRevisionError("The database has no readable Alembic revision (missing `alembic_version` table or "
                                     "unreachable database). " + UPGRADE_HINT) from None
    return [str(row) for row in rows]


async def assert_database_at_expected_revision(engine: AsyncEngine, script_location: Path = SCRIPT_LOCATION) -> str:
    """Raise MigrationRevisionError unless the database is exactly at the code's head revision; returns that revision."""
    expected = expected_revision(script_location)
    recorded = await database_revisions(engine)
    if len(recorded) != 1:
        _refuse(None, expected, f"recorded {len(recorded)} revisions")
        raise MigrationRevisionError(f"The database records {len(recorded)} Alembic revisions; exactly one is required "
                                     f"(expected {expected}). {UPGRADE_HINT if not recorded else RESTORE_HINT}")
    current = recorded[0]
    if current == expected:
        return current
    scripts = _scripts(script_location)
    try:
        known = scripts.get_revision(current) is not None
    except CommandError:  # unknown or malformed revision id (alembic ResolutionError)
        known = False
    if known:
        _refuse(current, expected, "database behind")
        raise MigrationRevisionError(f"Database revision {current} is older than the code's revision {expected}. "
                                     + UPGRADE_HINT)
    _refuse(current, expected, "database unknown or newer")
    raise MigrationRevisionError(f"Database revision {current} is unknown to this code (expected {expected}); the "
                                 "database is newer than the code or from another branch. " + RESTORE_HINT)


def _refuse(current: str | None, expected: str, reason: str) -> None:
    logger.error("db.revision_mismatch", database_revision=current, expected_revision=expected, reason=reason)
