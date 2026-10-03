import hashlib
import sqlite3

from tests.conftest import TEST_DB_PATH

# AI-owned tables change legitimately during AI operations; business-state comparisons exclude them.
AI_OWNED_TABLES = ("ai_pending_actions", "policy_chunks", "ai_conversations", "ai_conversation_messages",
                   "ai_audit_events")


def db_snapshot(*, exclude: tuple[str, ...] = ()) -> str:
    """Hash of the test database content (schema + every row), optionally excluding some tables."""
    connection = sqlite3.connect(f"{TEST_DB_PATH.as_uri()}?mode=ro", uri=True)
    try:
        lines = [
            line for line in connection.iterdump()
            if not any(f'"{table}"' in line or f" {table} " in line or f" {table}(" in line for table in exclude)
        ]
        return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    finally:
        connection.close()


def business_snapshot() -> str:
    return db_snapshot(exclude=AI_OWNED_TABLES)
