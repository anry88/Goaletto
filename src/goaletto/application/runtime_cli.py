"""Preview activation by default; --apply explicitly releases durable season jobs."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from goaletto.application.match_runtime import RUNTIME_KINDS, activate_season
from goaletto.config import Settings
from goaletto.infrastructure.database import Database


async def _activate(season_id: UUID) -> None:
    database = Database(Settings())
    try:
        async with database.transaction() as session:
            await activate_season(session, season_id, datetime.now(UTC))
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or activate regulation jobs for one season"
    )
    parser.add_argument("--season", required=True, type=UUID)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        try:
            asyncio.run(_activate(args.season))
        except Exception as error:
            parser.exit(1, f"Activation failed ({type(error).__name__}); no secrets printed\n")
    print(
        json.dumps(
            {
                "season_id": str(args.season),
                "supported_jobs": sorted(RUNTIME_KINDS),
                "database_written": args.apply,
                "rollover": "held",
                "sender": "not implemented",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
