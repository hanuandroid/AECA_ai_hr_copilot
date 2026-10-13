"""Create a verified backup of the database, uploads and non-secret configuration (production hardening Phase 2: backups).

Usage (from backend/, PYTHONPATH=.):
  python scripts/backup.py --destination /backups                           create and verify one backup
  python scripts/backup.py --destination /backups --keep-days 30            also LIST backups past retention
  python scripts/backup.py --destination /backups --keep-days 30 --prune    and delete them

Uses SQLite's online backup API, so it is safe while the API is running. The destination must be protected like the
live database (it contains personal and payroll data); encryption and off-host copies are open decisions.
Prints counts, sizes and paths only.
"""

import argparse
import shutil
import sys
from pathlib import Path

from app.core.config import settings
from app.ops.backup import (BackupError, backups_older_than, create_backup, safe_config_snapshot, sqlite_path_from_url,
                            verify_backup)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--destination", required=True, type=Path, help="directory that holds backups")
    parser.add_argument("--keep-days", type=int, default=None, help="backup retention in days (provisional default 30)")
    parser.add_argument("--prune", action="store_true", help="delete backups older than --keep-days (default: list only)")
    args = parser.parse_args()
    if args.prune and args.keep_days is None:
        parser.error("--prune requires --keep-days")

    upload_dirs = {
        "hr-policies": Path(settings.policy_upload_dir),
        "employee-documents": Path(settings.employee_document_upload_dir),
        "profile-photos": Path(settings.profile_photo_upload_dir),
    }
    try:
        result = create_backup(database_path=sqlite_path_from_url(settings.database_url), destination_root=args.destination,
                               upload_dirs=upload_dirs, config_snapshot=safe_config_snapshot(settings))
        verify_backup(result.path)
    except BackupError as exc:
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"backup ok: {result.path} ({result.files} files, {result.total_bytes} bytes, "
          f"alembic {result.alembic_revision}, checksums and integrity verified)")

    if args.keep_days is not None:
        expired = backups_older_than(args.destination, args.keep_days)
        for path in expired:
            if args.prune:
                shutil.rmtree(path)
            print(f"{'deleted' if args.prune else 'past retention (not deleted; use --prune)'}: {path.name}")
        if not expired:
            print(f"no backups older than {args.keep_days} days")
    return 0


if __name__ == "__main__":
    sys.exit(main())
