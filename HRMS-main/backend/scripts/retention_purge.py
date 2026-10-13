"""AI data retention cleanup (production hardening B-14). DRY RUN unless --apply is given.

Usage (from backend/, PYTHONPATH=.):
  python scripts/retention_purge.py                                    dry run: counts only
  python scripts/retention_purge.py --apply --operator-employee-id 1   delete; the operator must be an ACTIVE ADMIN

Purges conversations (with their messages) and terminal or expired pending actions past the configured retention.
Never deletes audit events (counted only) and never deletes EXECUTING/UNKNOWN actions (see stale_actions_report.py).
Production runs require separate approval of the retention periods.
"""

import argparse
import asyncio
import json
import sys

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.services.ai.retention import RetentionAuthorizationError, run_retention


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    parser.add_argument("--operator-employee-id", type=int, help="id of the ACTIVE ADMIN running the purge")
    args = parser.parse_args()
    configure_logging()
    async with SessionLocal() as db:
        try:
            report = await run_retention(db, apply=args.apply, operator_employee_id=args.operator_employee_id)
        except RetentionAuthorizationError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
    print(json.dumps(report.as_dict(), indent=2))
    if not report.applied:
        print("dry run: nothing was deleted (use --apply --operator-employee-id <admin id>)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
