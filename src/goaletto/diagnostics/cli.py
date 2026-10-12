"""Read-only diagnostics CLI for jobs, matches and the notification outbox.

The command opens a single read connection, runs bounded SELECT statements
and prints aggregates plus findings. It never writes rows, never claims work,
never extends ownership and never creates journey/user effects. It never
calls view use cases that write receipts.

No tokens, DSNs, Telegram IDs, message contents or raw exception text are
printed on any path: failures report the exception class name only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from goaletto.config import Settings
from goaletto.diagnostics.checks import (
    collect_report,
    describe_codes,
    render_human,
    report_to_dict,
)
from goaletto.diagnostics.queries import collect_snapshot
from goaletto.infrastructure.database import Database

DEFAULT_LATE_THRESHOLD_SECONDS = 180.0
DEFAULT_APPEARANCE_LOW_THRESHOLD = 100
DEFAULT_ROW_LIMIT = 2000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostics for runtime jobs, matches and outbox"
    )
    parser.add_argument(
        "--late-threshold-seconds",
        type=float,
        default=DEFAULT_LATE_THRESHOLD_SECONDS,
        help="Explicit lateness threshold: a pending job or match due moment older "
        "than this many seconds counts as overdue/late (default 180).",
    )
    parser.add_argument(
        "--appearance-low-threshold",
        type=int,
        default=DEFAULT_APPEARANCE_LOW_THRESHOLD,
        help="Explicit low-stock threshold for free unique appearance identities.",
    )
    parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="Output rendering (default human).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_ROW_LIMIT,
        help="Bounded rows per diagnostic scope; hitting it marks coverage incomplete.",
    )
    parser.add_argument(
        "--season",
        type=UUID,
        default=None,
        help="Scope calendar scopes to one season UUID; sender scopes stay global.",
    )
    parser.add_argument(
        "--list-codes",
        action="store_true",
        help="Print the finite finding-code set without touching the database.",
    )
    return parser


async def _collect_report_text(
    *,
    late_threshold_seconds: float,
    appearance_low_threshold: int,
    row_limit: int,
    season_id: UUID | None,
    output_format: str,
) -> str:
    database = Database(Settings())
    try:
        async with database.engine.connect() as connection:
            snapshot = await collect_snapshot(
                connection,
                now=datetime.now(UTC),
                limit=row_limit,
                season_id=season_id,
            )
    finally:
        await database.close()
    report = collect_report(
        snapshot,
        late_threshold_seconds=late_threshold_seconds,
        appearance_low_threshold=appearance_low_threshold,
        row_limit=row_limit,
    )
    if output_format == "json":
        return json.dumps(report_to_dict(report), indent=2, sort_keys=False) + "\n"
    return render_human(report)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.list_codes:
        print(describe_codes())
        return
    try:
        text = asyncio.run(
            _collect_report_text(
                late_threshold_seconds=args.late_threshold_seconds,
                appearance_low_threshold=args.appearance_low_threshold,
                row_limit=args.limit,
                season_id=args.season,
                output_format=args.format,
            )
        )
    except Exception as error:
        # Never print DSNs, tokens, chat IDs or raw exception text.
        parser.exit(1, f"Diagnostics failed ({type(error).__name__}); no secrets printed\n")
    print(text)


if __name__ == "__main__":
    main()
