"""Bounded read-only DB snapshots for Prometheus; no raw rows or personal labels."""

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from prometheus_client.core import GaugeMetricFamily, Metric
from sqlalchemy import text

from goaletto.application.analytics import Reason, Surface
from goaletto.infrastructure.database import Database

WINDOWS = (1, 7, 30)
JOB_KINDS = frozenset(
    (
        "system.ping",
        "match.lock_lineup",
        "match.start",
        "match.tick",
        "match.second_half_start",
        "match.extra_start",
        "match.extra_second_half_start",
        "match.penalty",
        "match.finalize_result",
        "match.remind",
        "ownership.remind",
        "ownership.expire",
        "training.apply",
        "finance.cycle",
        "calendar.materialize",
        "stadium.complete",
        "market.close",
        "market.cancel_player",
        "feedback.cleanup",
        "feedback.scan",
        "season.rollover",
        "scout.complete",
        "scout.expire",
        "staff.career_expire",
        "world.reserve",
        "world.catch_up",
        "runtime.reconcile",
    )
)
STATUSES = frozenset(("pending", "done", "failed", "held", "sent"))
COMPETITIONS = frozenset(("league", "cup", "champions", "friendly"))
DELIVERY_KINDS = frozenset(
    (
        "fixture_reminder",
        "match_board",
        "match_result",
        "match_recovery",
        "ownership_reminder",
        "ownership_expired",
        "feedback_offer",
        "training_cancelled",
        "scout_report",
        "staff_career_offer",
    )
)


def bounded(value: Any, allowed: frozenset[str]) -> str:
    return str(value) if str(value) in allowed else "other"


# Every dimension is an enum; campaign IDs, arbitrary sources, targets and set IDs
# are deliberately omitted. Counts from collapsed rows are summed, not overwritten.
SPECS: dict[str, tuple[tuple[str, ...], dict[str, frozenset[str]]]] = {
    "entry": (
        (
            "starts",
            "eligible",
            "waiting",
            "claimed",
            "ready",
            "unknown_claimed_late",
            "unknown_ready_late",
        ),
        {},
    ),
    "sources": (
        ("starts", "eligible", "waiting", "claimed", "ready"),
        {"kind": frozenset(("direct", "known", "unknown"))},
    ),
    "retention": (
        ("starts", "eligible", "waiting", "retained", "unknown_late"),
        {"day": frozenset(("1", "3", "7", "30"))},
    ),
    "opportunities": (
        ("managers", "participated_n", "unknown_late"),
        {
            "stage": frozenset(("first", "second")),
            "state": frozenset(
                (
                    "eligible",
                    "waiting",
                    "unavailable",
                    "no_opportunity",
                    "mature",
                    "coverage",
                    "unsupported",
                    "interrupted",
                    "no_calendar",
                    "no_first",
                    "future",
                    "missed",
                    "participated",
                    "unknown",
                )
            ),
        },
    ),
    "last_steps": (
        ("managers", "offers_accepted"),
        {
            "last_step": frozenset(
                (
                    "start_accepted",
                    "entry_opened",
                    "match_report_opened",
                    "match_command_accepted",
                    "club_claimed",
                    "lineup_saved",
                    "match_opened",
                    "command_accepted",
                    "match_participated",
                    "screen_opened",
                    "help_opened",
                    "feedback_answered",
                )
            )
        },
    ),
    "recovery": (
        (
            "errors",
            "managers",
            "eligible_N",
            "waiting",
            "help_accepted",
            "help_unconfirmed",
            "recovered_n",
        ),
        {
            "reason": frozenset(r.value for r in Reason),
            "surface": frozenset(s.value for s in Surface),
        },
    ),
    "delivery": (
        ("total", "delivered", "failed_known", "failed_unknown", "pending", "unknown_messages"),
        {"kind": DELIVERY_KINDS},
    ),
    "delivery_attempts": (
        ("count",),
        {
            "outcome": frozenset(
                (
                    "sent",
                    "edited",
                    "duplicate",
                    "rate_limited",
                    "transient",
                    "unknown",
                    "blocked",
                    "chat_unavailable",
                    "invalid_request",
                )
            )
        },
    ),
    "feedback": (
        (
            "offered_N",
            "delivery_confirmed",
            "delivery_unconfirmed",
            "answered_n",
            "skipped_n",
            "opted_out_n",
            "redacted_unknown",
            "expired_unanswered",
            "waiting",
        ),
        {"internal": frozenset(("True", "False"))},
    ),
    "feedback_reasons": (
        ("answers",),
        {"reason": frozenset(r.value for r in Reason), "internal": frozenset(("True", "False"))},
    ),
}


def report_family(name: str, reports: dict[str, dict[str, Any]]) -> Metric:
    fields, dimensions = SPECS[name]
    family = GaugeMetricFamily(
        "goaletto_analytics_" + name,
        "Current fixed-window SQL snapshot, not an event counter or a historical cohort query.",
        labels=["window", *dimensions, "measure"],
    )
    values: defaultdict[tuple[str, ...], float] = defaultdict(float)
    for window, report in reports.items():
        for row in report["rows"]:
            labels = (
                window,
                *(bounded(row.get(key), allowed) for key, allowed in dimensions.items()),
            )
            for field in fields:
                value = row.get(field)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                ):
                    values[(*labels, field)] += value
    for labels, value in sorted(values.items()):
        family.add_metric(list(labels), value)
    return family


async def collect_snapshot(database: Database, at: datetime) -> tuple[Metric, ...]:
    families: list[Metric] = []
    async with database.read_transaction() as session:
        await session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
        queues = GaugeMetricFamily(
            "goaletto_queue_items",
            "Durable queue state; due/oldest apply only to pending items.",
            labels=["queue", "status", "measure"],
        )
        for table, queue in (("jobs", "jobs"), ("outbox_messages", "outbox")):
            rows = (
                await session.execute(
                    text(f"""SELECT status,count(*) AS items,
                count(*) FILTER (WHERE status='pending' AND due_at<=:at) AS due,
                max(greatest(0,extract(epoch FROM CAST(:at AS timestamptz)-due_at)))
                  FILTER (WHERE status='pending' AND due_at<=:at) AS age
                FROM {table} GROUP BY status"""),
                    {"at": at},
                )
            ).mappings()
            by_status = {row["status"]: row for row in rows}
            for status in (
                ("pending", "done", "failed", "held")
                if queue == "jobs"
                else ("pending", "sent", "failed")
            ):
                queue_row = by_status.get(status)
                for measure, key in (
                    ("items", "items"),
                    ("due", "due"),
                    ("oldest_due_seconds", "age"),
                ):
                    queues.add_metric(
                        [queue, status, measure],
                        float((queue_row.get(key) if queue_row is not None else 0) or 0),
                    )
        families.append(queues)

        matches = GaugeMetricFamily(
            "goaletto_fixtures",
            "Current scheduled/live/paused/finished fixture counts.",
            labels=["competition", "status"],
        )
        rows = (
            await session.execute(
                text(
                    "SELECT coalesce(competition,'league') AS competition,status,count(*) AS n "
                    "FROM fixtures GROUP BY 1,2"
                )
            )
        ).mappings()
        counts = {(row["competition"], row["status"]): row["n"] for row in rows}
        for competition in sorted(COMPETITIONS):
            for status in ("scheduled", "cancelled", "live", "paused", "finished", "abandoned"):
                matches.add_metric([competition, status], counts.get((competition, status), 0))
        families.append(matches)
        clubs = GaugeMetricFamily(
            "goaletto_clubs",
            "Current club ownership, not an acquisition cohort.",
            labels=["tier", "ownership"],
        )
        rows = (
            await session.execute(
                text(
                    "SELECT l.tier,(c.owner_telegram_id IS NOT NULL) AS owned,count(*) AS n "
                    "FROM clubs c JOIN leagues l ON l.id=c.league_id GROUP BY 1,2"
                )
            )
        ).mappings()
        counts_clubs = {(str(row["tier"]), bool(row["owned"])): row["n"] for row in rows}
        for tier in range(1, 6):
            for owned in (False, True):
                clubs.add_metric(
                    [str(tier), "owned" if owned else "unowned"],
                    counts_clubs.get((str(tier), owned), 0),
                )
        families.append(clubs)

        reports: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in SPECS}
        coverage = GaugeMetricFamily(
            "goaletto_analytics_coverage",
            "Collection coverage for each fixed cohort window.",
            labels=["window", "measure"],
        )
        for days in WINDOWS:
            window = f"{days}d"
            for name in SPECS:
                value = await session.scalar(
                    text("SELECT goaletto_operator.report(:name,:since,:at,'{}'::jsonb)"),
                    {"name": name, "since": at - timedelta(days=days), "at": at},
                )
                report = json.loads(value) if isinstance(value, str) else value
                if not isinstance(report, dict) or not isinstance(report.get("rows"), list):
                    raise ValueError("Invalid aggregate report")
                reports[name][window] = report
                if name == "entry":
                    coverage.add_metric([window, "available"], int(report["state"] == "available"))
                    coverage.add_metric(
                        [window, "limited"], int(bool(report.get("coverage_limited")))
                    )
                    for key in ("coverage_started_at", "available_from", "report_at"):
                        if report.get(key):
                            coverage.add_metric(
                                [window, key], datetime.fromisoformat(report[key]).timestamp()
                            )
        families.append(coverage)
        families.extend(report_family(name, windows) for name, windows in reports.items())
    return tuple(families)
