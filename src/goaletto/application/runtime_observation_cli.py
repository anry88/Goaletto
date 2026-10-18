"""Private read-only operator latency aggregates; preview never loads settings."""

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from goaletto.application.analytics import require_utc
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.runtime_observation import report


def utc(value: str) -> datetime:
    at = datetime.fromisoformat(value)
    require_utc(at)
    return at


async def apply(since: datetime, at: datetime) -> dict[str, Any]:
    database = Database(Settings())
    try:
        async with database.read_transaction() as session:
            return await report(session, since, at)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Goaletto technical latency")
    parser.add_argument("--since", type=utc, required=True)
    parser.add_argument("--at", type=utc, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.since >= args.at:
        parser.error("Observation window must be positive")
    if args.apply:
        try:
            result = asyncio.run(apply(args.since, args.at))
        except Exception as error:
            parser.exit(1, f"Runtime report failed ({type(error).__name__}); no secrets printed\n")
    else:
        result = {"database_connected": False, "apply_required": True}
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
