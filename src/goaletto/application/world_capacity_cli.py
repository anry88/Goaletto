"""Read-only launch check: ready club reserve and incomplete world preparations."""

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import func, select

from goaletto.application.world_capacity import LOW_WATER, RESERVE_TARGET, available_clubs
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import WorldAdmission


async def status() -> dict[str, Any]:
    database = Database(Settings())
    try:
        async with database.read_transaction() as session:
            available = await available_clubs(session)
            preparing = int(
                await session.scalar(
                    select(func.count())
                    .select_from(WorldAdmission)
                    .where(WorldAdmission.ready_at.is_(None))
                )
                or 0
            )
            return {
                "ready_free_tier5": available,
                "minimum_reserve": LOW_WATER,
                "high_water": RESERVE_TARGET,
                "preparing_sets": preparing,
                "reserve_healthy": available >= LOW_WATER,
                "prewarmed_for_launch": available > LOW_WATER and preparing == 0,
            }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Goaletto club reserve check")
    parser.add_argument("--apply", action="store_true", help="Read the configured game database")
    args = parser.parse_args()
    if not args.apply:
        result = {"database_connected": False, "apply_required": True}
    else:
        try:
            result = asyncio.run(status())
        except Exception as error:
            parser.exit(1, f"Reserve check failed ({type(error).__name__}); no secrets printed\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
