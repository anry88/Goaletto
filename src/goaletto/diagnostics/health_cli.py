"""Small read-only Docker/operator worker health check; no network API."""

import argparse
import asyncio
import json
from uuid import UUID

from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.worker_health import status


async def check(settings: Settings, max_age: float) -> dict[str, object]:
    instance = UUID(settings.worker_health_file.read_text(encoding="ascii").strip())
    database = Database(settings)
    try:
        async with asyncio.timeout(5):
            return await status(database, instance, sender=settings.outbox_enabled, max_age=max_age)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only current worker instance health")
    parser.add_argument("--max-age-seconds", type=int, default=120)
    args = parser.parse_args()
    if not 30 <= args.max_age_seconds <= 900:
        parser.error("max-age-seconds must be 30..900")
    try:
        report = asyncio.run(check(Settings(), args.max_age_seconds))
    except Exception as error:
        report = {"healthy": False, "check": "unavailable", "error_class": type(error).__name__}
    print(json.dumps(report))
    raise SystemExit(0 if report["healthy"] else 1)


if __name__ == "__main__":
    main()
