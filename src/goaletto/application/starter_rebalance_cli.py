"""Preview or apply the audited starter-squad-v2 migration; never reset game state."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text

from goaletto.application.starter_rebalance import apply, plan
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import DataProtection


async def run(*, applying: bool, fingerprint: str | None) -> dict[str, Any]:
    settings = Settings()
    db = Database(settings)
    try:
        async with db.read_transaction() as session:
            if await session.scalar(text("SELECT current_database()")) != "goaletto":
                raise ValueError("Migration requires the Goaletto application database")
            protected = await session.scalar(select(DataProtection.protected_at))
            if settings.environment == "production" and protected is None:
                raise ValueError("Production data protection must remain sealed")
            _, summary = await plan(session)
        if applying:
            if not fingerprint:
                raise ValueError("Apply requires the fingerprint from a fresh preview")
            async with db.transaction() as session:
                summary = await apply(
                    session, at=datetime.now(UTC), expected_fingerprint=fingerprint
                )
        return {
            "database": "goaletto",
            "environment": settings.environment,
            "protected": protected is not None,
            **summary,
        }
    finally:
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-fingerprint")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(applying=args.apply, fingerprint=args.expected_fingerprint))
    except Exception as error:
        parser.exit(1, f"Starter migration stopped ({type(error).__name__}); no secrets printed\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
