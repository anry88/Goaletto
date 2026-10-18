"""Fixed aggregate SQL: runtime latency is separate from calendar/tick waiting."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc

SQL = """
WITH requests AS (
 SELECT * FROM runtime_requests WHERE received_at >= :since AND received_at <= :at
), samples(metric, seconds) AS (
 SELECT 'request_commit', CASE WHEN committed_at <= :at THEN
   extract(epoch FROM committed_at-received_at) END FROM requests
 UNION ALL SELECT 'request_render', CASE WHEN rendered_at <= :at THEN
   extract(epoch FROM rendered_at-render_started_at) END FROM requests
 UNION ALL SELECT 'request_telegram', CASE WHEN finished_at <= :at AND
   delivery_outcome IN ('accepted','rejected','unknown') THEN
   extract(epoch FROM finished_at-network_started_at) END FROM requests
 UNION ALL SELECT 'request_total', CASE WHEN finished_at <= :at THEN
   extract(epoch FROM finished_at-received_at) END FROM requests
 UNION ALL SELECT 'worker_processing', extract(epoch FROM finished_at-started_at)
   FROM operation_attempts WHERE started_at BETWEEN :since AND :at AND finished_at <= :at
   AND stage LIKE 'match.%'
 UNION ALL SELECT 'worker_lateness', greatest(0, extract(epoch FROM started_at-due_at))
   FROM operation_attempts WHERE started_at BETWEEN :since AND :at AND finished_at <= :at
   AND stage LIKE 'match.%'
 UNION ALL SELECT 'delivery_enqueue_to_claim',
   extract(epoch FROM a.started_at-greatest(o.enqueued_at,a.available_at))
   FROM outbox_delivery_attempts a JOIN outbox_messages o ON o.id=a.outbox_id
   WHERE a.started_at BETWEEN :since AND :at AND a.available_at IS NOT NULL
   AND o.enqueued_at IS NOT NULL
 UNION ALL SELECT 'delivery_render', CASE WHEN rendered_at <= :at THEN
   extract(epoch FROM rendered_at-render_started_at) END
   FROM outbox_delivery_attempts WHERE started_at BETWEEN :since AND :at
 UNION ALL SELECT 'delivery_telegram', CASE WHEN finished_at <= :at THEN
   extract(epoch FROM finished_at-network_started_at) END
   FROM outbox_delivery_attempts WHERE started_at BETWEEN :since AND :at
 UNION ALL SELECT 'command_wait_for_tick', CASE WHEN applied_at <= :at THEN
   extract(epoch FROM applied_at-accepted_at) END
   FROM accepted_commands WHERE accepted_at BETWEEN :since AND :at
), metrics AS (
 SELECT metric, count(*) AS observations,
   count(*) FILTER (WHERE seconds >= 0) AS measured,
   count(*) FILTER (WHERE seconds IS NULL OR seconds < 0) AS unknown,
   percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds) FILTER (WHERE seconds >= 0) AS p50_seconds,
   percentile_cont(0.95) WITHIN GROUP (ORDER BY seconds) FILTER (WHERE seconds >= 0) AS p95_seconds
 FROM samples GROUP BY metric
)
SELECT * FROM metrics ORDER BY metric
"""


async def report(session: AsyncSession, since: datetime, at: datetime) -> dict[str, Any]:
    require_utc(since)
    require_utc(at)
    if since >= at:
        raise ValueError("Observation window must be positive")
    metrics = [
        dict(row)
        for row in (await session.execute(text(SQL), {"since": since, "at": at})).mappings()
    ]
    names = (
        "request_commit",
        "request_render",
        "request_telegram",
        "request_total",
        "worker_processing",
        "worker_lateness",
        "delivery_enqueue_to_claim",
        "delivery_render",
        "delivery_telegram",
        "command_wait_for_tick",
    )
    by_name = {row["metric"]: row for row in metrics}
    metrics = [
        by_name.get(
            name,
            {
                "metric": name,
                "observations": 0,
                "measured": 0,
                "unknown": 0,
                "p50_seconds": None,
                "p95_seconds": None,
            },
        )
        for name in names
    ]
    states = (
        await session.execute(
            text("""
      SELECT CASE WHEN committed_at <= :at THEN 'committed'
        WHEN business_outcome='rejected' AND finished_at <= :at THEN 'rejected'
        ELSE 'unknown' END AS business_outcome,
        CASE WHEN finished_at IS NULL OR finished_at > :at
        THEN CASE WHEN network_started_at <= :at THEN 'unknown' ELSE 'unfinished' END
        ELSE delivery_outcome END AS delivery_outcome, count(*) AS attempts
      FROM runtime_requests WHERE received_at BETWEEN :since AND :at
      GROUP BY 1,2 ORDER BY 1,2
    """),
            {"since": since, "at": at},
        )
    ).mappings()
    reconciliation = await session.scalar(
        text("""
      SELECT jsonb_build_object('last_scan_at', payload->'last_scan_at',
        'scanned', payload->'scanned', 'restored', payload->'restored',
        'corrupt', payload->'corrupt', 'conflicts', payload->'conflicts', 'status', status)
      FROM jobs WHERE dedupe_key='runtime-reconcile:v1'
    """)
    )
    return {
        "reconciliation": reconciliation,
        "since": since.isoformat(),
        "at": at.isoformat(),
        "metrics": metrics,
        "request_outcomes": [dict(row) for row in states],
        "scope": "operational attempts; includes internal accounts; no user retention",
        "tick_wait_is_technical_latency": False,
    }
