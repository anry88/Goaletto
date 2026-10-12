"""Campaign links/aggregates as local operator CLI; never starts polling."""

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import UTC, date, datetime

from goaletto.application.acquisition import SOURCES, campaign_link, create_campaign
from goaletto.config import Settings
from goaletto.infrastructure.acquisition_queries import source_funnel
from goaletto.infrastructure.database import Database


async def _apply(args: argparse.Namespace) -> dict[str, object]:
    database = Database(Settings())
    try:
        async with database.transaction() as session:
            if args.command == "create":
                campaign = await create_campaign(
                    session, args.source, args.placement, args.published_on, datetime.now(UTC)
                )
                return {
                    "campaign_id": campaign.id,
                    "link": campaign_link(campaign.code, args.bot_username),
                }
            report = await source_funnel(session, args.cohort_from, args.at)
            return asdict(report)
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview campaign registry or read an aggregate source funnel"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--source", choices=sorted(SOURCES), required=True)
    create.add_argument("--placement", required=True)
    create.add_argument("--published-on", type=date.fromisoformat, required=True)
    create.add_argument("--bot-username", default="GoalettoBot")
    create.add_argument("--apply", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("--cohort-from", type=datetime.fromisoformat, required=True)
    report.add_argument("--at", type=datetime.fromisoformat, required=True)
    report.add_argument("--apply", action="store_true", help="Read configured game DB")
    args = parser.parse_args()
    if args.apply:
        try:
            data = asyncio.run(_apply(args))
        except Exception as error:
            parser.exit(
                1, f"Campaign operation failed ({type(error).__name__}); no secrets printed\n"
            )
    else:
        data = {"command": args.command, "database_connected": False, "apply_required": True}
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
