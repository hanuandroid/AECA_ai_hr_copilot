"""SQLite online backup, verification and restore (production hardening Phase 2, B-17).

A backup is a directory ``hrms-backup-<UTC timestamp>/`` containing:
- ``hrms.db``: a consistent copy made with SQLite's online backup API (safe while the API is running), checked with
  ``PRAGMA integrity_check``;
- ``uploads/<name>/...``: the persistent upload directories (policies, employee documents, profile photos);
- ``config.json``: a snapshot of NON-secret settings (secret-like keys are excluded, never masked);
- ``manifest.json``: sha256 and size of every file above, the Alembic revision and the integrity result.

Restore always targets an explicit destination directory that must be empty (or absent); it verifies every checksum
before copying and runs the integrity check on the restored database. It never writes to the configured live paths
on its own: switching the service to restored files is a separate, deliberate operator step.

Open decisions (see docs/production-hardening/backup-restore.md): encryption at rest, backup destination, access
control and off-host copies. Backups contain personal and payroll data and must be protected like the live database.
"""

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

BACKUP_PREFIX = "hrms-backup-"
_BACKUP_NAME_RE = re.compile(r"^hrms-backup-(\d{8}T\d{6}Z)$")
DB_FILE = "hrms.db"
MANIFEST_FILE = "manifest.json"
CONFIG_FILE = "config.json"
UPLOADS_DIR = "uploads"
MANIFEST_VERSION = 1
# Settings whose names match this are never written to a backup (excluded, not masked). database_url can embed
# credentials for non-SQLite databases.
_SECRET_NAME_RE = re.compile(r"(secret|key|password|token|credential|database_url)", re.IGNORECASE)


class BackupError(Exception):
    """A backup or restore precondition or verification failed. Messages never include file contents."""


@dataclass(frozen=True)
class BackupResult:
    path: Path
    files: int
    total_bytes: int
    alembic_revision: str | None


def sqlite_path_from_url(database_url: str) -> Path:
    """Filesystem path of a sqlite / sqlite+aiosqlite URL."""
    match = re.match(r"^sqlite(\+\w+)?:///(.+)$", database_url)
    if not match:
        raise BackupError("only SQLite database URLs are supported")
    return Path(match.group(2))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def integrity_check(db_path: Path) -> str:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    finally:
        connection.close()
    return "ok" if rows == [("ok",)] else "failed"


def _alembic_revision(db_path: Path) -> str | None:
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return row[0] if row else None


def safe_config_snapshot(settings: Any) -> dict[str, Any]:
    """Non-secret settings only. Secret-like names are dropped entirely (not masked)."""
    snapshot = {}
    for name, value in settings.model_dump().items():
        if _SECRET_NAME_RE.search(name) or hasattr(value, "get_secret_value"):
            continue
        snapshot[name] = value
    return snapshot


def _online_backup(source: Path, target: Path) -> None:
    if not source.is_file():
        raise BackupError("source database not found")
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _files_under(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def create_backup(*, database_path: Path, destination_root: Path, upload_dirs: dict[str, Path],
                  config_snapshot: dict[str, Any], now: datetime | None = None) -> BackupResult:
    now = now or datetime.now(UTC)
    destination_root.mkdir(parents=True, exist_ok=True)
    backup_dir = destination_root / f"{BACKUP_PREFIX}{now.strftime('%Y%m%dT%H%M%SZ')}"
    if backup_dir.exists():
        raise BackupError("a backup with this timestamp already exists")
    work_dir = destination_root / f".{backup_dir.name}.partial"
    shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir()
    try:
        db_copy = work_dir / DB_FILE
        _online_backup(database_path, db_copy)
        integrity = integrity_check(db_copy)
        if integrity != "ok":
            raise BackupError("integrity check failed on the backup copy")
        for name, source in upload_dirs.items():
            target = work_dir / UPLOADS_DIR / name
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                target.mkdir(parents=True)  # record that the directory was empty/absent
        (work_dir / CONFIG_FILE).write_text(json.dumps(config_snapshot, sort_keys=True, indent=2, default=str))

        files = {path.relative_to(work_dir).as_posix(): {"sha256": _sha256(path), "bytes": path.stat().st_size}
                 for path in _files_under(work_dir)}
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "alembic_revision": _alembic_revision(db_copy),
            "database_integrity": integrity,
            "upload_dirs": sorted(upload_dirs),
            "files": files,
        }
        (work_dir / MANIFEST_FILE).write_text(json.dumps(manifest, sort_keys=True, indent=2))
        work_dir.rename(backup_dir)  # appears complete or not at all
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
    return BackupResult(backup_dir, len(files), sum(item["bytes"] for item in files.values()), manifest["alembic_revision"])


def verify_backup(backup_dir: Path) -> dict[str, Any]:
    """Check every manifest checksum, that no file is missing or unexpected, and the database integrity."""
    manifest_path = backup_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        raise BackupError("manifest missing")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise BackupError("unsupported manifest version")
    expected = manifest["files"]
    actual = {path.relative_to(backup_dir).as_posix() for path in _files_under(backup_dir)} - {MANIFEST_FILE}
    if actual != set(expected):
        raise BackupError("backup contents do not match the manifest (missing or unexpected files)")
    for relative, meta in expected.items():
        if _sha256(backup_dir / relative) != meta["sha256"]:
            raise BackupError(f"checksum mismatch: {relative}")
    if integrity_check(backup_dir / DB_FILE) != "ok":
        raise BackupError("integrity check failed")
    return manifest


def restore_backup(backup_dir: Path, destination: Path) -> dict[str, Any]:
    """Restore into an explicit, empty (or absent) destination directory. Returns the verified manifest."""
    manifest = verify_backup(backup_dir)
    if destination.exists() and any(destination.iterdir()):
        raise BackupError("restore destination is not empty")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_dir / DB_FILE, destination / DB_FILE)
    shutil.copytree(backup_dir / UPLOADS_DIR, destination / UPLOADS_DIR)
    for relative, meta in manifest["files"].items():
        if relative == CONFIG_FILE:
            continue  # reference only; configuration is applied through the environment, not restored
        if _sha256(destination / relative) != meta["sha256"]:
            raise BackupError(f"restored file checksum mismatch: {relative}")
    if integrity_check(destination / DB_FILE) != "ok":
        raise BackupError("integrity check failed on the restored database")
    return manifest


def backups_older_than(destination_root: Path, keep_days: int, now: datetime | None = None) -> list[Path]:
    """Backup directories (by their name's timestamp) older than keep_days. Only names this tool creates match."""
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=keep_days)
    expired = []
    for path in sorted(destination_root.iterdir()) if destination_root.is_dir() else []:
        match = _BACKUP_NAME_RE.match(path.name)
        if path.is_dir() and match:
            created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            if created < cutoff:
                expired.append(path)
    return expired
