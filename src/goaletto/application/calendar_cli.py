"""Operator announcement/recovery; previews roll back unless --apply is explicit."""

import argparse
import asyncio
from datetime import UTC, datetime
from uuid import UUID

from goaletto.application.calendar_planning import plan_horizon, recover_unannounced_plan
from goaletto.config import Settings
from goaletto.infrastructure.database import Database


async def run(season_id: UUID, *, apply: bool, recover_plan: UUID | None, day: str | None) -> int:
    database = Database(Settings())

    class PreviewRollback(Exception):
        pass

    count = 0
    try:
        try:
            async with database.transaction() as session:
                at = datetime.now(UTC)
                if recover_plan is not None:
                    if day is None:
                        raise ValueError("Recovery needs a UTC date")
                    count = await recover_unannounced_plan(
                        session, recover_plan, datetime.fromisoformat(day).replace(tzinfo=UTC), at
                    )
                else:
                    count = await plan_horizon(session, season_id, at)
                if not apply:
                    raise PreviewRollback()
        except PreviewRollback:
            pass
        return count
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Goaletto ten-day calendar")
    parser.add_argument("--season-id", type=UUID, required=True)
    parser.add_argument("--recover-plan", type=UUID)
    parser.add_argument("--day", help="New UTC date for explicit recovery, YYYY-MM-DD")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        count = asyncio.run(
            run(args.season_id, apply=args.apply, recover_plan=args.recover_plan, day=args.day)
        )
    except Exception as error:
        raise SystemExit(f"Calendar operation failed ({type(error).__name__})") from None
    print(f"{'Applied' if args.apply else 'Preview rolled back'}: {count} new fixtures")


if __name__ == "__main__":
    main()
