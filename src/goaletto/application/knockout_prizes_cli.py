"""Preview or pay historical knockout placements from current editions."""

import argparse
import asyncio
import json
from collections import Counter
from datetime import UTC, datetime

from goaletto.application.analytics import require_utc
from goaletto.application.knockout_prizes import (
    Prize,
    backfill_current_prizes,
    unpaid_current_prizes,
)
from goaletto.config import Settings
from goaletto.infrastructure.database import Database


async def run(before: datetime, apply: bool) -> list[Prize]:
    at = datetime.now(UTC)
    require_utc(before)
    if before > at:
        raise ValueError("Prize cutoff is in the future")
    database = Database(Settings())
    try:
        if apply:
            async with database.transaction() as session:
                return await backfill_current_prizes(session, before, at)
        async with database.read_transaction() as session:
            return await unpaid_current_prizes(session, before)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=datetime.fromisoformat, help="UTC cutoff")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        prizes = asyncio.run(run(args.before, args.apply))
    except ValueError as error:
        parser.exit(1, f"Knockout prizes: {error}\n")
    except Exception as error:
        parser.exit(1, f"Knockout prizes failed ({type(error).__name__})\n")
    print(
        json.dumps(
            {
                "applied": args.apply,
                "before": args.before.isoformat(),
                "awards": Counter(p.details["competition"] for p in prizes),
                "clubs": len({p.club_id for p in prizes}),
                "total": sum(p.amount for p in prizes),
            }
        )
    )


if __name__ == "__main__":
    main()
