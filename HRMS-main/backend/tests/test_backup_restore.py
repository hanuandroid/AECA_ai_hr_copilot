"""Backup, verification and restore tooling (production hardening Phase 2, B-17)."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ops.backup import (BackupError, backups_older_than, create_backup, restore_backup, safe_config_snapshot,
                            sqlite_path_from_url, verify_backup)
from tests.conftest import TEST_DB_PATH

NOW = datetime(2026, 10, 13, 2, 0, 0, tzinfo=UTC)


@pytest.fixture
def uploads(tmp_path: Path) -> dict[str, Path]:
    policies = tmp_path / "src" / "hr-policies"
    (policies / "nested").mkdir(parents=True)
    (policies / "leave.md").write_text("leave policy")
    (policies / "nested" / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    return {"hr-policies": policies, "profile-photos": tmp_path / "src" / "absent"}


def _backup(tmp_path: Path, uploads: dict[str, Path], now: datetime = NOW):
    return create_backup(database_path=TEST_DB_PATH, destination_root=tmp_path / "backups", upload_dirs=uploads,
                         config_snapshot={"environment": "test"}, now=now)


def test_backup_contains_consistent_db_uploads_and_manifest(tmp_path, uploads):
    result = _backup(tmp_path, uploads)
    assert result.path.name == "hrms-backup-20261013T020000Z"
    manifest = verify_backup(result.path)
    assert manifest["database_integrity"] == "ok"
    assert manifest["alembic_revision"] == "0021_hr_policy_publication"
    assert {"hrms.db", "config.json", "uploads/hr-policies/leave.md", "uploads/hr-policies/nested/doc.pdf"} <= set(manifest["files"])
    assert (result.path / "uploads" / "profile-photos").is_dir()  # absent source recorded as empty
    with sqlite3.connect(result.path / "hrms.db") as connection:
        assert connection.execute("SELECT count(*) FROM employees").fetchone()[0] >= 5
    assert not list((tmp_path / "backups").glob(".*partial"))


def test_tampered_backup_fails_verification_and_is_not_restored(tmp_path, uploads):
    result = _backup(tmp_path, uploads)
    (result.path / "uploads" / "hr-policies" / "leave.md").write_text("tampered")
    with pytest.raises(BackupError, match="checksum mismatch"):
        verify_backup(result.path)
    with pytest.raises(BackupError):
        restore_backup(result.path, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_unexpected_or_missing_files_fail_verification(tmp_path, uploads, change):
    result = _backup(tmp_path, uploads)
    if change == "extra":
        (result.path / "uploads" / "hr-policies" / "planted.sh").write_text("x")
    else:
        (result.path / "uploads" / "hr-policies" / "leave.md").unlink()
    with pytest.raises(BackupError, match="manifest"):
        verify_backup(result.path)


def test_restore_targets_an_explicit_empty_destination_and_verifies(tmp_path, uploads):
    result = _backup(tmp_path, uploads)
    destination = tmp_path / "restore" / "drill"
    manifest = restore_backup(result.path, destination)
    assert manifest["alembic_revision"] == "0021_hr_policy_publication"
    assert (destination / "uploads" / "hr-policies" / "nested" / "doc.pdf").read_bytes() == b"%PDF-1.4 fake"
    with sqlite3.connect(destination / "hrms.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restore_refuses_a_non_empty_destination(tmp_path, uploads):
    result = _backup(tmp_path, uploads)
    destination = tmp_path / "live"
    destination.mkdir()
    (destination / "hrms.db").write_text("current data")
    with pytest.raises(BackupError, match="not empty"):
        restore_backup(result.path, destination)
    assert (destination / "hrms.db").read_text() == "current data"


def test_config_snapshot_excludes_secrets():
    settings = Settings(jwt_secret_key="s" * 40, anthropic_api_key="sk-ant-should-not-appear",
                        ai_audit_fingerprint_key="f" * 48, database_url="sqlite+aiosqlite:///./x.db")
    snapshot = safe_config_snapshot(settings)
    rendered = json.dumps(snapshot, default=str)
    for name in ("jwt_secret_key", "anthropic_api_key", "ai_audit_fingerprint_key", "database_url"):
        assert name not in snapshot
    for value in ("s" * 40, "sk-ant-should-not-appear", "f" * 48):
        assert value not in rendered
    assert snapshot["environment"] == settings.environment and "ai_sql_max_rows" in snapshot


def test_backup_retention_lists_only_tool_created_backups_past_the_window(tmp_path, uploads):
    _backup(tmp_path, uploads, now=NOW - timedelta(days=31))
    _backup(tmp_path, uploads, now=NOW - timedelta(days=29))
    (tmp_path / "backups" / "unrelated-old-dir").mkdir()
    expired = backups_older_than(tmp_path / "backups", keep_days=30, now=NOW)
    assert [path.name for path in expired] == [f"hrms-backup-{(NOW - timedelta(days=31)).strftime('%Y%m%dT%H%M%SZ')}"]


@pytest.mark.parametrize("url, expected", [
    ("sqlite+aiosqlite:///./storage/hrms.db", "storage/hrms.db"),
    ("sqlite+aiosqlite:////app/storage/hrms.db", "/app/storage/hrms.db"),
    ("sqlite:///C:/data/hrms.db", "C:/data/hrms.db"),
])
def test_sqlite_path_from_url(url, expected):
    assert sqlite_path_from_url(url) == Path(expected)


def test_non_sqlite_urls_are_refused():
    with pytest.raises(BackupError):
        sqlite_path_from_url("postgresql://user:pw@db/hrms")
