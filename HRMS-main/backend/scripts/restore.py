"""Verify a backup and restore it into an EXPLICIT, empty destination directory (production hardening B-17).

Usage (from backend/, PYTHONPATH=.):
  python scripts/restore.py --backup /backups/hrms-backup-20261013T020000Z --destination /restore/drill-1
  python scripts/restore.py --backup /backups/hrms-backup-20261013T020000Z --verify-only

Never writes to the configured live database or upload paths. Switching a service to the restored files is a
separate operator step (stop the API, move the files into place, start it, check /api/v1/health and alembic current).
"""

import argparse
import sys
from pathlib import Path

from app.ops.backup import BackupError, restore_backup, verify_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backup", required=True, type=Path, help="a hrms-backup-<timestamp> directory")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--destination", type=Path, help="empty or absent directory to restore into")
    target.add_argument("--verify-only", action="store_true", help="verify checksums and integrity only")
    args = parser.parse_args()
    try:
        if args.verify_only:
            manifest = verify_backup(args.backup)
            print(f"backup verified: {len(manifest['files'])} files, alembic {manifest['alembic_revision']}")
        else:
            manifest = restore_backup(args.backup, args.destination)
            print(f"restored to {args.destination}: {len(manifest['files'])} files verified, "
                  f"alembic {manifest['alembic_revision']}, integrity ok")
    except BackupError as exc:
        print(f"RESTORE FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
