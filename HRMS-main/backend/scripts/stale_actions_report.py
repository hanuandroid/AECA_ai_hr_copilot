"""Read-only report of confirmed AI actions whose outcome needs a human check (production hardening B-11).

Usage (from backend/, PYTHONPATH=.):  python scripts/stale_actions_report.py [--minutes 15]

Lists UNKNOWN actions (execution deadline exceeded) and EXECUTING actions claimed more than --minutes ago.
Identifiers and timestamps only. Changes nothing and retries nothing: verify each item in the HR system.
Exit code 0 when there is nothing to review, 3 when there is.
"""

import argparse
import asyncio
import json
import sys

from app.db.session import SessionLocal
from app.services.ai.actions.reconciliation import DEFAULT_EXECUTING_THRESHOLD_MINUTES, actions_needing_review


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=int, default=DEFAULT_EXECUTING_THRESHOLD_MINUTES)
    args = parser.parse_args()
    async with SessionLocal() as db:
        items = await actions_needing_review(db, executing_threshold_minutes=args.minutes)
    print(json.dumps({"actions_needing_review": items}, indent=2))
    return 3 if items else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
