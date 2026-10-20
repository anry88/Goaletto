"""Inspect or permanently seal the production database. There is no unlock operation."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.data_protection import protect
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import DataProtection


async def seal(session: AsyncSession, *, environment: str, at: datetime) -> None:
    if environment != "production":
        raise ValueError("Sealing requires production mode")
    if await session.scalar(text("SELECT current_database()")) != "goaletto":
        raise ValueError("Sealing requires the Goaletto database")
    await protect(session, at)


async def run(*, apply: bool) -> dict[str, Any]:
    settings = Settings()
    database = Database(settings)
    try:
        if apply:
            async with database.transaction() as session:
                await seal(session, environment=settings.environment, at=datetime.now(UTC))
        async with database.read_transaction() as session:
            protected_at = await session.scalar(select(DataProtection.protected_at))
            return {
                "database": await session.scalar(text("SELECT current_database()")),
                "protected": protected_at is not None,
                "protected_at": protected_at.isoformat() if protected_at else None,
                "development_reset_allowed": (
                    settings.environment == "development" and protected_at is None
                ),
            }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seal", action="store_true", help="Permanently forbid production development resets"
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(run(apply=args.seal))
    except Exception as error:
        parser.exit(
            1, f"Protection operation failed ({type(error).__name__}); no secrets printed\n"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
