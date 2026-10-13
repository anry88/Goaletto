"""Bounded PostgreSQL aggregates for operator analytics reports.

Read-only. Aggregate output only, explicit UTC observation time, no
Telegram identifiers or free-text payloads. Journey queries exclude
internal managers and analytics opt-out tombstones, honour
``recorded_at <= :report_at`` (late arrivals recount later windows),
deduplicate by distinct manager/message, and clip the requested cohort to
``coverage_started_at`` and journey retention. Delivery history is
operational: it is independent of journey retention/opt-out and never
converts a missing outcome into success/failure.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.analytics_reports import (
    Coverage,
    DeliveryKindRow,
    DeliveryReport,
    EntryFunnel,
    RetentionReport,
    RetentionRow,
    resolve_coverage,
    validate_delivery_report,
    validate_entry_funnel,
    validate_retention_report,
)
from goaletto.infrastructure.analytics import CoverageNotStarted
from goaletto.infrastructure.models import AnalyticsCoverage

ENTRY_FUNNEL_SQL = """
WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at,
        a.started_at + INTERVAL '24 hours' <= :report_at AS mature
 FROM acquisition_first_touches a
 JOIN analytics_managers am ON am.id = a.analytics_manager_id
 JOIN managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND NOT EXISTS (SELECT 1 FROM analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= :cohort_from AND a.started_at <= :report_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.name = 'club_claimed' AND e.origin = 'user'
   AND e.occurred_at >= c.started_at AND e.occurred_at < c.started_at + INTERVAL '24 hours'
   AND e.recorded_at <= :report_at) AS claimed,
 EXISTS (SELECT 1 FROM journey_events claim
   JOIN journey_events saved
     ON saved.analytics_manager_id = claim.analytics_manager_id
    AND saved.envelope ->> 'ownership_id' = claim.envelope ->> 'ownership_id'
   WHERE claim.analytics_manager_id = c.analytics_manager_id
   AND claim.version = 1 AND claim.name = 'club_claimed' AND claim.origin = 'user'
   AND saved.version = 1 AND saved.name = 'lineup_saved' AND saved.origin = 'user'
   AND saved.occurred_at >= claim.occurred_at
   AND claim.occurred_at >= c.started_at
   AND saved.occurred_at < c.started_at + INTERVAL '24 hours'
   AND claim.recorded_at <= :report_at AND saved.recorded_at <= :report_at) AS ready,
 EXISTS (SELECT 1 FROM journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.name = 'club_claimed' AND e.origin = 'user'
   AND e.occurred_at >= c.started_at AND e.occurred_at < c.started_at + INTERVAL '24 hours'
 ) AS claimed_eventual,
 EXISTS (SELECT 1 FROM journey_events claim
   JOIN journey_events saved
     ON saved.analytics_manager_id = claim.analytics_manager_id
    AND saved.envelope ->> 'ownership_id' = claim.envelope ->> 'ownership_id'
   WHERE claim.analytics_manager_id = c.analytics_manager_id
   AND claim.version = 1 AND claim.name = 'club_claimed' AND claim.origin = 'user'
   AND saved.version = 1 AND saved.name = 'lineup_saved' AND saved.origin = 'user'
   AND saved.occurred_at >= claim.occurred_at
   AND claim.occurred_at >= c.started_at
   AND saved.occurred_at < c.started_at + INTERVAL '24 hours'
 ) AS ready_eventual
 FROM cohort c
)
SELECT COUNT(*) AS starts,
 COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND claimed) AS claimed,
 COUNT(*) FILTER (WHERE mature AND ready) AS ready,
 COUNT(*) FILTER (WHERE mature AND NOT claimed AND claimed_eventual) AS unknown_claimed_late,
 COUNT(*) FILTER (WHERE mature AND NOT ready AND ready_eventual) AS unknown_ready_late
FROM effects
"""

RETENTION_SQL_TEMPLATE = """
WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at,
        a.started_at + INTERVAL '{high} hours' <= :report_at AS mature
 FROM acquisition_first_touches a
 JOIN analytics_managers am ON am.id = a.analytics_manager_id
 JOIN managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND NOT EXISTS (SELECT 1 FROM analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= :cohort_from AND a.started_at <= :report_at
), effects AS (
 SELECT c.*,
 EXISTS (SELECT 1 FROM journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '{low} hours'
   AND e.occurred_at < c.started_at + INTERVAL '{high} hours'
   AND e.recorded_at <= :report_at) AS retained,
 EXISTS (SELECT 1 FROM journey_events e
   WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.occurred_at >= c.started_at + INTERVAL '{low} hours'
   AND e.occurred_at < c.started_at + INTERVAL '{high} hours'
 ) AS retained_eventual
 FROM cohort c
)
SELECT COUNT(*) AS starts,
 COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND retained) AS retained,
 COUNT(*) FILTER (WHERE mature AND NOT retained AND retained_eventual) AS unknown_late
FROM effects
"""

RETENTION_WINDOWS = ((1, 24, 48), (3, 72, 96), (7, 168, 192), (30, 720, 744))

DELIVERY_MESSAGES_SQL = """
SELECT payload ->> 'kind' AS kind,
 COUNT(*) AS total,
 COUNT(*) FILTER (WHERE status = 'sent') AS delivered,
 COUNT(*) FILTER (WHERE status = 'failed' AND last_error IS DISTINCT FROM 'unknown_outcome')
   AS failed_known,
 COUNT(*) FILTER (WHERE status = 'failed' AND last_error = 'unknown_outcome')
   AS failed_unknown,
 COUNT(*) FILTER (WHERE status = 'pending') AS pending,
 COUNT(*) FILTER (WHERE status != 'sent' AND (
   last_error = 'unknown_outcome' OR EXISTS (
     SELECT 1 FROM outbox_delivery_attempts a
     WHERE a.outbox_id = outbox_messages.id AND a.outcome = 'unknown'
   ))) AS unknown_messages
FROM outbox_messages
WHERE due_at >= :window_from AND due_at <= :report_at
GROUP BY payload ->> 'kind'
ORDER BY payload ->> 'kind'
"""

DELIVERY_ATTEMPTS_SQL = """
SELECT outcome, COUNT(*) AS count
FROM outbox_delivery_attempts
WHERE outbox_id IN (
 SELECT id FROM outbox_messages WHERE due_at >= :window_from AND due_at <= :report_at
)
AND started_at >= :window_from AND started_at <= :report_at
GROUP BY outcome
ORDER BY outcome
"""


async def _coverage(
    session: AsyncSession, requested_from: datetime, report_at: datetime
) -> Coverage:
    require_utc(requested_from)
    require_utc(report_at)
    coverage = await session.scalar(select(AnalyticsCoverage).where(AnalyticsCoverage.version == 1))
    if coverage is None:
        raise CoverageNotStarted("Collection has not started")
    return resolve_coverage(
        coverage_started_at=coverage.started_at,
        retention_days=coverage.retention_days,
        requested_from=requested_from,
        report_at=report_at,
    )


async def entry_funnel(
    session: AsyncSession,
    cohort_from: datetime,
    report_at: datetime,
) -> EntryFunnel:
    coverage = await _coverage(session, cohort_from, report_at)
    result = await session.execute(
        text(ENTRY_FUNNEL_SQL),
        {"cohort_from": coverage.available_from, "report_at": coverage.report_at},
    )
    row = dict(result.mappings().one())
    report = EntryFunnel(
        coverage=coverage,
        starts=int(row["starts"]),
        eligible=int(row["eligible"]),
        waiting=int(row["waiting"]),
        claimed=int(row["claimed"]),
        ready=int(row["ready"]),
        unknown_claimed_late=int(row["unknown_claimed_late"]),
        unknown_ready_late=int(row["unknown_ready_late"]),
    )
    validate_entry_funnel(report.as_dict())
    return report


async def retention(
    session: AsyncSession,
    cohort_from: datetime,
    report_at: datetime,
) -> RetentionReport:
    coverage = await _coverage(session, cohort_from, report_at)
    rows: list[RetentionRow] = []
    starts: int | None = None
    for day, low, high in RETENTION_WINDOWS:
        result = await session.execute(
            text(RETENTION_SQL_TEMPLATE.format(low=low, high=high)),
            {"cohort_from": coverage.available_from, "report_at": coverage.report_at},
        )
        row = dict(result.mappings().one())
        if starts is None:
            starts = int(row["starts"])
        elif starts != int(row["starts"]):
            raise ValueError("Retention cohort changed between day windows")
        rows.append(
            RetentionRow(
                day=day,
                eligible_N=int(row["eligible"]),
                waiting=int(row["waiting"]),
                retained_n=int(row["retained"]),
                unknown_late=int(row["unknown_late"]),
            )
        )
    report = RetentionReport(coverage=coverage, starts=starts or 0, rows=tuple(rows))
    validate_retention_report(report.as_dict())
    return report


async def delivery(
    session: AsyncSession,
    window_from: datetime,
    report_at: datetime,
) -> DeliveryReport:
    require_utc(window_from)
    require_utc(report_at)
    if window_from > report_at:
        raise ValueError("Delivery window starts after the observation time")
    messages = (
        (
            await session.execute(
                text(DELIVERY_MESSAGES_SQL), {"window_from": window_from, "report_at": report_at}
            )
        )
        .mappings()
        .all()
    )
    attempts = (
        (
            await session.execute(
                text(DELIVERY_ATTEMPTS_SQL), {"window_from": window_from, "report_at": report_at}
            )
        )
        .mappings()
        .all()
    )
    by_outcome = tuple(
        sorted(
            ((str(row["outcome"]), int(row["count"])) for row in attempts),
            key=lambda item: item[0],
        )
    )
    kinds = tuple(
        DeliveryKindRow(
            kind=str(row["kind"] or "unknown"),
            total=int(row["total"]),
            delivered=int(row["delivered"]),
            failed_known=int(row["failed_known"]),
            failed_unknown=int(row["failed_unknown"]),
            pending=int(row["pending"]),
        )
        for row in messages
    )
    total = sum(row.total for row in kinds)
    delivered = sum(row.delivered for row in kinds)
    failed_known = sum(row.failed_known for row in kinds)
    failed_unknown = sum(row.failed_unknown for row in kinds)
    pending = sum(row.pending for row in kinds)
    unknown = sum(int(row["unknown_messages"]) for row in messages)
    attempts_total = sum(count for _, count in by_outcome)
    lookup = dict(by_outcome)
    report = DeliveryReport(
        window_from=window_from,
        report_at=report_at,
        messages_total_N=total,
        messages_delivered_n=delivered,
        messages_failed_known=failed_known,
        messages_failed_unknown=failed_unknown,
        messages_pending_waiting=pending,
        messages_unknown=unknown,
        attempts_total=attempts_total,
        attempts_by_outcome=by_outcome,
        attempts_unknown=int(lookup.get("unknown", 0)),
        attempts_waiting=int(lookup.get("claimed", 0) + lookup.get("sending", 0)),
        kinds=kinds,
    )
    validate_delivery_report(report.as_dict())
    return report
