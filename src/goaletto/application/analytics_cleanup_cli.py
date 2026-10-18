"""Explicit operator retention cleanup; preview never opens the game database."""

import argparse
import asyncio
import json
from datetime import datetime

from goaletto.application.analytics import require_utc
from goaletto.config import Settings
from goaletto.infrastructure.analytics import cleanup_events
from goaletto.infrastructure.database import Database


def utc(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    require_utc(at)
    return at


async def apply(at: datetime) -> None:
    database = Database(Settings())
    try:
        async with database.transaction() as session:
            await cleanup_events(session, at)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply persisted journey retention; no game reset")
    parser.add_argument("--at", type=utc, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        try:
            asyncio.run(apply(args.at))
        except Exception as error:
            parser.exit(
                1, f"Analytics cleanup failed ({type(error).__name__}); no secrets printed\n"
            )
    print(json.dumps({"applied": args.apply, "at": args.at.isoformat()}))


if __name__ == "__main__":
    main()
