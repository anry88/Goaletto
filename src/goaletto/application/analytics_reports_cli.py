"""Operator analytics reports. Restricted aggregates only, never a public API.

Without ``--apply`` the CLI prints a preview and never connects to a database.
With ``--apply`` it opens the configured game database in a read transaction;
no state is written. Timestamps must be timezone-aware UTC ISO-8601.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from typing import Any

from goaletto.application.analytics import require_utc
from goaletto.config import Settings
from goaletto.infrastructure.analytics_reports_queries import delivery, entry_funnel, retention
from goaletto.infrastructure.database import Database


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    require_utc(parsed)
    return parsed


async def _apply(args: argparse.Namespace) -> dict[str, Any]:
    database = Database(Settings())
    try:
        async with database.read_transaction() as session:
            if args.command == "entry-funnel":
                report = await entry_funnel(session, args.cohort_from, args.at)
                result: dict[str, Any] = report.as_dict()
                return result
            if args.command == "retention":
                report_retention = await retention(session, args.cohort_from, args.at)
                result_retention: dict[str, Any] = report_retention.as_dict()
                return result_retention
            report_delivery = await delivery(session, args.window_from, args.at)
            result_delivery: dict[str, Any] = report_delivery.as_dict()
            return result_delivery
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Goaletto analytics reports")
    sub = parser.add_subparsers(dest="command", required=True)
    entry = sub.add_parser("entry-funnel", help="start -> claim -> manual lineup in 24h")
    entry.add_argument("--cohort-from", type=_utc, required=True)
    entry.add_argument("--at", type=_utc, required=True)
    entry.add_argument("--apply", action="store_true", help="Read configured game DB")
    kept = sub.add_parser("retention", help="D1/D3/D7/D30 explicit manager actions")
    kept.add_argument("--cohort-from", type=_utc, required=True)
    kept.add_argument("--at", type=_utc, required=True)
    kept.add_argument("--apply", action="store_true", help="Read configured game DB")
    sent = sub.add_parser("delivery", help="Outbox attempts and known/unknown outcomes")
    sent.add_argument("--window-from", type=_utc, required=True)
    sent.add_argument("--at", type=_utc, required=True)
    sent.add_argument("--apply", action="store_true", help="Read configured game DB")
    args = parser.parse_args()
    data: dict[str, Any]
    if args.apply:
        try:
            data = asyncio.run(_apply(args))
        except Exception as error:
            parser.exit(
                1, f"Analytics report failed ({type(error).__name__}); no secrets printed\n"
            )
            raise AssertionError("unreachable") from error
    else:
        data = {
            "command": args.command,
            "database_connected": False,
            "apply_required": True,
        }
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
