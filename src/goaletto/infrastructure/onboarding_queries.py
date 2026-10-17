"""Read-only, aggregate launch reports. No identities or arbitrary text leave SQL."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.infrastructure.analytics_reports_queries import _coverage

COHORT = """
WITH cohort AS (
 SELECT a.analytics_manager_id, a.started_at, am.manager_id, a.kind,
        a.campaign_id, m.locale
 FROM acquisition_first_touches a
 JOIN analytics_managers am ON am.id = a.analytics_manager_id
 JOIN managers m ON m.id = am.manager_id
 WHERE NOT m.is_internal
 AND NOT EXISTS (SELECT 1 FROM analytics_exclusions x WHERE x.manager_id = m.id)
 AND a.started_at >= :cohort_from AND a.started_at <= :report_at
)
"""

LAST_STEPS_SQL = (
    COHORT
    + """
SELECT c.kind AS source_kind, c.campaign_id, c.locale,
       date_trunc('week', c.started_at AT TIME ZONE 'UTC')::date AS cohort_week,
       CASE WHEN first_claim.claimed_at IS NULL THEN 'unclaimed'
            WHEN EXISTS (SELECT 1 FROM journey_events preview
              WHERE preview.analytics_manager_id = c.analytics_manager_id
              AND preview.version = 1 AND preview.name = 'club_preview_opened'
              AND preview.origin = 'user' AND preview.recorded_at <= :report_at
              AND preview.occurred_at >= c.started_at
              AND preview.occurred_at <= first_claim.claimed_at)
              THEN 'preview_claim' ELSE 'direct_claim' END AS entry_path,
       COALESCE(last_step.name, 'start_accepted') AS last_step,
       COALESCE(last_step.surface, 'onboarding') AS surface,
       COUNT(*) AS managers,
       COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM journey_events screen
         WHERE screen.analytics_manager_id = c.analytics_manager_id AND screen.version = 1
         AND screen.name = 'club_offer_delivered' AND screen.origin = 'system'
         AND screen.occurred_at >= c.started_at AND screen.occurred_at <= :report_at
         AND screen.recorded_at <= :report_at)) AS offers_accepted
FROM cohort c
LEFT JOIN LATERAL (
 SELECT MIN(e.occurred_at) AS claimed_at FROM journey_events e
 WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
 AND e.name = 'club_claimed' AND e.origin = 'user' AND e.recorded_at <= :report_at
 AND e.occurred_at >= c.started_at AND e.occurred_at <= :report_at
) first_claim ON true
LEFT JOIN LATERAL (
 SELECT e.name, e.envelope ->> 'surface' AS surface
 FROM journey_events e
 WHERE e.analytics_manager_id = c.analytics_manager_id AND e.version = 1
 AND e.origin = 'user' AND e.user_activity
 AND e.occurred_at >= c.started_at AND e.occurred_at <= :report_at
 AND e.recorded_at <= :report_at
 ORDER BY e.occurred_at DESC, e.recorded_at DESC, e.operation_id, e.effect_number DESC
 LIMIT 1
) last_step ON true
GROUP BY c.kind, c.campaign_id, c.locale, cohort_week, entry_path, last_step.name, last_step.surface
ORDER BY c.kind, c.campaign_id, c.locale, last_step.name, last_step.surface
"""
)

RECOVERY_SQL = (
    COHORT
    + """
, errors AS (
 SELECT e.*, e.envelope ->> 'reason' AS reason,
        e.envelope ->> 'surface' AS surface,
        COALESCE(e.envelope ->> 'target', e.envelope ->> 'surface') AS target,
        e.occurred_at + INTERVAL '30 minutes' <= :report_at AS mature
 FROM cohort c JOIN journey_events e ON e.analytics_manager_id = c.analytics_manager_id
 WHERE e.version = 1 AND e.name = 'action_rejected' AND e.origin = 'user'
 AND e.occurred_at >= c.started_at AND e.occurred_at <= :report_at
 AND e.recorded_at <= :report_at
), outcomes AS (
 SELECT r.*,
 EXISTS (SELECT 1 FROM journey_events h
   WHERE h.operation_id = r.operation_id AND h.analytics_manager_id = r.analytics_manager_id
   AND h.version = 1 AND h.name = 'help_delivered' AND h.origin = 'system'
   AND h.occurred_at >= r.occurred_at AND h.occurred_at <= :report_at
   AND h.recorded_at <= :report_at) AS helped,
 EXISTS (SELECT 1 FROM journey_events s
   WHERE s.analytics_manager_id = r.analytics_manager_id AND s.version = 1
   AND s.origin = 'user' AND s.user_activity
   AND ((r.target = 'onboarding' AND s.name = 'club_claimed')
     OR (r.target = 'squad' AND s.name = 'lineup_saved'))
   AND (r.envelope ->> 'ownership_id' IS NULL
     OR s.envelope ->> 'ownership_id' = r.envelope ->> 'ownership_id')
   AND s.occurred_at >= r.occurred_at
   AND s.occurred_at < r.occurred_at + INTERVAL '30 minutes'
   AND s.recorded_at <= :report_at) AS recovered
 FROM errors r
)
SELECT reason, surface, target, COUNT(*) AS errors,
 COUNT(DISTINCT analytics_manager_id) AS managers,
 COUNT(*) FILTER (WHERE mature) AS "eligible_N",
 COUNT(*) FILTER (WHERE NOT mature) AS waiting,
 COUNT(*) FILTER (WHERE helped) AS help_accepted,
 COUNT(*) FILTER (WHERE NOT helped) AS help_unconfirmed,
 COUNT(*) FILTER (WHERE mature AND recovered) AS recovered_n
FROM outcomes GROUP BY reason, surface, target ORDER BY reason, surface, target
"""
)

# Plans cover the full season, including dates outside the ten-day announcement
# horizon. Their UTC date only orders candidates; it never invents a kickoff.
OPPORTUNITIES_SQL = (
    COHORT
    + """
, calendar AS (
 SELECT p.id, p.season_id, p.league_id, p.home_club_id, p.away_club_id,
        p.assigned_day, f.status, f.scheduled_start_at, f.lineup_lock_at,
        f.actual_start_at, f.actual_finish_at
 FROM fixture_plans p LEFT JOIN fixtures f ON f.id = p.id
 WHERE p.competition = 'league'
 UNION ALL
 SELECT f.id, f.season_id, f.league_id, f.home_club_id, f.away_club_id,
        (f.scheduled_start_at AT TIME ZONE 'UTC')::date, f.status,
        f.scheduled_start_at, f.lineup_lock_at, f.actual_start_at, f.actual_finish_at
 FROM fixtures f WHERE f.competition = 'league'
 AND NOT EXISTS (SELECT 1 FROM fixture_plans p WHERE p.id = f.id)
), candidates AS (
 SELECT c.analytics_manager_id, c.started_at, o.id AS ownership_id, o.claimed_at,
        LEAST(o.ended_at, o.expires_at) AS ownership_end, p.*,
        ROW_NUMBER() OVER (PARTITION BY c.analytics_manager_id ORDER BY
          COALESCE(p.actual_start_at, p.scheduled_start_at,
            p.assigned_day::timestamp AT TIME ZONE 'UTC'), p.id) AS ordinal
 FROM cohort c JOIN ownership_periods o ON o.manager_id = c.manager_id
 JOIN calendar p ON p.home_club_id = o.club_id OR p.away_club_id = o.club_id
 WHERE o.claimed_at <= :report_at
 AND o.claimed_at <= COALESCE(p.lineup_lock_at,
     (p.assigned_day + 1)::timestamp AT TIME ZONE 'UTC')
 AND LEAST(o.ended_at, o.expires_at) > COALESCE(p.lineup_lock_at,
     p.assigned_day::timestamp AT TIME ZONE 'UTC')
 AND p.status IS DISTINCT FROM 'cancelled'
), classified AS (
 SELECT p.*,
 CASE
 WHEN p.claimed_at < p.started_at THEN 'coverage'
 WHEN (SELECT COUNT(*) FROM calendar full_cal
       WHERE full_cal.season_id = p.season_id AND full_cal.league_id = p.league_id) <> 90
   THEN 'coverage'
 WHEN p.status IS NULL OR p.status IN ('scheduled','live','paused') THEN 'waiting'
 WHEN p.status = 'abandoned' THEN 'unsupported'
 WHEN p.actual_start_at IS NULL OR p.actual_finish_at IS NULL
   OR NOT EXISTS (SELECT 1 FROM match_results r
     WHERE r.fixture_id = p.id AND r.recorded_at <= :report_at)
   THEN 'unavailable'
 WHEN p.actual_finish_at + INTERVAL '24 hours' > :report_at THEN 'waiting'
 WHEN p.ownership_end < p.actual_finish_at + INTERVAL '24 hours' THEN 'interrupted'
 ELSE 'mature' END AS state,
 EXISTS (SELECT 1 FROM journey_events e
   WHERE e.analytics_manager_id = p.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.name IN ('match_opened','match_report_opened','match_command_accepted')
   AND e.envelope ->> 'fixture_id' = p.id::text
   AND e.envelope ->> 'ownership_id' = p.ownership_id::text
   AND e.occurred_at >= GREATEST(p.claimed_at, p.actual_start_at - INTERVAL '24 hours')
   AND e.occurred_at < p.actual_finish_at + INTERVAL '24 hours'
   AND e.recorded_at <= :report_at) AS participated,
 EXISTS (SELECT 1 FROM journey_events e
   WHERE e.analytics_manager_id = p.analytics_manager_id AND e.version = 1
   AND e.origin = 'user' AND e.user_activity
   AND e.name IN ('match_opened','match_report_opened','match_command_accepted')
   AND e.envelope ->> 'fixture_id' = p.id::text
   AND e.envelope ->> 'ownership_id' = p.ownership_id::text
   AND e.occurred_at >= GREATEST(p.claimed_at, p.actual_start_at - INTERVAL '24 hours')
   AND e.occurred_at < p.actual_finish_at + INTERVAL '24 hours'
   AND e.recorded_at > :report_at) AS late
 FROM candidates p WHERE ordinal <= 2
), per_manager AS (
 SELECT c.analytics_manager_id, COALESCE(f.state, 'no_calendar') AS first_state,
        COALESCE(f.participated, false) AS first_participated,
        COALESCE(f.late, false) AS first_late,
        CASE WHEN f.state = 'mature' AND f.participated THEN COALESCE(s.state, 'no_calendar')
             ELSE 'no_first' END AS second_state,
        COALESCE(s.participated, false) AS second_participated,
        COALESCE(s.late, false) AS second_late
 FROM cohort c LEFT JOIN classified f
 ON f.analytics_manager_id = c.analytics_manager_id AND f.ordinal = 1
 LEFT JOIN classified s ON s.analytics_manager_id = c.analytics_manager_id AND s.ordinal = 2
), stages AS (
 SELECT 'first' AS stage, first_state AS state, first_participated AS participated,
        first_late AS late FROM per_manager
 UNION ALL
 SELECT 'second', second_state, second_participated, second_late FROM per_manager
)
SELECT stage, state, COUNT(*) AS managers,
 COUNT(*) FILTER (WHERE state = 'mature' AND participated) AS participated_n,
 COUNT(*) FILTER (WHERE state = 'mature' AND NOT participated AND late) AS unknown_late
FROM stages GROUP BY stage, state ORDER BY stage, state
"""
)


async def launch_report(
    session: AsyncSession, cohort_from: datetime, report_at: datetime, *, report: str
) -> dict[str, Any]:
    coverage = await _coverage(session, cohort_from, report_at)
    queries = {
        "last-steps": LAST_STEPS_SQL,
        "recovery": RECOVERY_SQL,
        "match-opportunities": OPPORTUNITIES_SQL,
    }
    if report not in queries:
        raise ValueError("Unknown launch report")
    result = await session.execute(
        text(queries[report]), {"cohort_from": coverage.available_from, "report_at": report_at}
    )
    rows = [dict(row) for row in result.mappings()]
    for row in rows:
        if any(type(value) is int and value < 0 for value in row.values()):
            raise ValueError("Negative aggregate")
        if report == "recovery" and not (
            row["waiting"] + row["eligible_N"] == row["errors"]
            and row["help_accepted"] + row["help_unconfirmed"] == row["errors"]
            and 0 <= row["recovered_n"] <= row["eligible_N"]
        ):
            raise ValueError("Recovery denominator mismatch")
        if report == "match-opportunities" and not (
            row["participated_n"] + row["unknown_late"] <= row["managers"]
        ):
            raise ValueError("Opportunity denominator mismatch")
    summary: dict[str, Any] = {}
    if report == "match-opportunities":
        totals = {
            stage: sum(r["managers"] for r in rows if r["stage"] == stage)
            for stage in ("first", "second")
        }
        if totals["first"] != totals["second"]:
            raise ValueError("Opportunity cohorts do not reconcile")
        for stage in ("first", "second"):
            mature = next((r for r in rows if r["stage"] == stage and r["state"] == "mature"), None)
            eligible = mature["managers"] if mature else 0
            participated = mature["participated_n"] if mature else 0
            summary[stage] = {
                "eligible_N": eligible,
                "participated_n": participated,
                "rate": participated / eligible if eligible else None,
            }
        summary["total_managers"] = totals["first"]
    return {
        "report": report,
        "summary": summary,
        "contract_version": 1,
        "coverage_started_at": coverage.coverage_started_at.isoformat(),
        "available_from": coverage.available_from.isoformat(),
        "requested_from": cohort_from.isoformat(),
        "report_at": report_at.isoformat(),
        "coverage_limited": coverage.coverage_limited,
        "semantics": (
            "observed_actions_only; missing_confirmation_is_unknown; no_inferred_dropout_reason"
        ),
        "rows": rows,
    }
