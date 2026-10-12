"""Restricted source funnel. Aggregate output only; explicit UTC observation time."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.infrastructure.analytics import CoverageNotStarted
from goaletto.infrastructure.models import AnalyticsCoverage

SOURCE_FUNNEL_SQL = """
WITH cohort AS (
 SELECT a.analytics_manager_id, a.kind, a.campaign_id, a.started_at,
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
   AND claim.recorded_at <= :report_at AND saved.recorded_at <= :report_at) AS ready
 FROM cohort c
)
SELECT e.kind, e.campaign_id, p.source, p.placement, p.published_on,
 COUNT(*) AS starts, COUNT(*) FILTER (WHERE mature) AS eligible,
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE mature AND claimed) AS claimed,
 COUNT(*) FILTER (WHERE mature AND ready) AS ready
FROM effects e LEFT JOIN acquisition_campaigns p ON p.id = e.campaign_id
GROUP BY e.kind, e.campaign_id, p.source, p.placement, p.published_on
ORDER BY e.kind, e.campaign_id
"""


@dataclass(frozen=True)
class SourceFunnelReport:
    coverage_started_at: datetime
    requested_from: datetime
    available_from: datetime
    report_at: datetime
    coverage_limited: bool
    rows: tuple[dict[str, Any], ...]


def validate_funnel_row(row: dict[str, Any]) -> None:
    allowed = {
        "kind",
        "campaign_id",
        "source",
        "placement",
        "published_on",
        "starts",
        "eligible",
        "waiting",
        "claimed",
        "ready",
    }
    if set(row) != allowed or row["kind"] not in {"direct", "unknown", "known"}:
        raise ValueError("Unknown source aggregate fields")
    if (row["kind"] == "known") != (row["campaign_id"] is not None):
        raise ValueError("Source aggregate lost its attribution class")
    counts = [row[k] for k in ("starts", "eligible", "waiting", "claimed", "ready")]
    if any(type(v) is not int or v < 0 for v in counts):
        raise ValueError("Source aggregate counts must be nonnegative integers")
    if (
        row["eligible"] + row["waiting"] != row["starts"]
        or not 0 <= row["ready"] <= row["claimed"] <= row["eligible"]
    ):
        raise ValueError("Source funnel numerator/denominator do not reconcile")


async def source_funnel(
    session: AsyncSession,
    cohort_from: datetime,
    report_at: datetime,
) -> SourceFunnelReport:
    require_utc(cohort_from)
    require_utc(report_at)
    if cohort_from > report_at:
        raise ValueError("Cohort starts after the observation time")
    coverage = await session.scalar(select(AnalyticsCoverage).where(AnalyticsCoverage.version == 1))
    if coverage is None:
        raise CoverageNotStarted("Collection has not started")
    available_from = max(
        cohort_from, coverage.started_at, report_at - timedelta(days=coverage.retention_days)
    )
    result = await session.execute(
        text(SOURCE_FUNNEL_SQL),
        {
            "cohort_from": available_from,
            "report_at": report_at,
        },
    )
    rows = tuple(dict(row) for row in result.mappings())
    for row in rows:
        validate_funnel_row(row)
    return SourceFunnelReport(
        coverage.started_at,
        cohort_from,
        available_from,
        report_at,
        available_from > cohort_from,
        rows,
    )
