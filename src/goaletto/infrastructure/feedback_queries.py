"""Aggregate voluntary responses. No comment text, manager refs or inferred reasons."""

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc


async def feedback_report(session: AsyncSession, since: datetime, at: datetime) -> dict[str, Any]:
    require_utc(since)
    require_utc(at)
    if since >= at:
        raise ValueError("Feedback observation window must be positive")
    rows = [
        dict(row)
        for row in (
            await session.execute(
                text("""
      SELECT c.slug AS campaign, m.is_internal AS internal,
        COUNT(*) AS "offered_N",
        COUNT(*) FILTER (WHERE r.delivered_at <= :at) AS delivery_confirmed,
        COUNT(*) FILTER (WHERE r.delivered_at IS NULL OR r.delivered_at > :at)
          AS delivery_unconfirmed,
        COUNT(*) FILTER (WHERE r.status='answered' AND r.responded_at <= :at) AS answered_n,
        COUNT(*) FILTER (WHERE r.status='skipped' AND r.responded_at <= :at) AS skipped_n,
        COUNT(*) FILTER (WHERE r.status='opted_out' AND x.kind='opt_out'
          AND x.excluded_at <= :at) AS opted_out_n,
        COUNT(*) FILTER (WHERE r.status='opted_out' AND
          (x.excluded_at IS NULL OR x.excluded_at > :at)) AS redacted_unknown,
        COUNT(*) FILTER (WHERE r.status <> 'opted_out'
          AND (r.responded_at IS NULL OR r.responded_at > :at)
          AND r.expires_at <= :at) AS expired_unanswered,
        COUNT(*) FILTER (WHERE r.status <> 'opted_out'
          AND (r.responded_at IS NULL OR r.responded_at > :at)
          AND (r.expires_at IS NULL OR r.expires_at > :at)) AS waiting
      FROM feedback_responses r JOIN feedback_campaigns c ON c.id=r.campaign_id
      JOIN managers m ON m.id=r.manager_id
      LEFT JOIN analytics_exclusions x ON x.manager_id=m.id
      WHERE r.prompted_at >= :since AND r.prompted_at <= :at
      GROUP BY c.slug,m.is_internal ORDER BY c.slug,m.is_internal
    """),
                {"since": since, "at": at},
            )
        ).mappings()
    ]
    for row in rows:
        total = row["offered_N"]
        row["answer_rate"] = row["answered_n"] / total if total else None
        row["skip_rate"] = row["skipped_n"] / total if total else None
    reasons = [
        dict(row)
        for row in (
            await session.execute(
                text("""
      SELECT c.slug AS campaign, m.is_internal AS internal, r.reason, COUNT(*) AS answers
      FROM feedback_responses r JOIN feedback_campaigns c ON c.id=r.campaign_id
      JOIN managers m ON m.id=r.manager_id
      WHERE r.prompted_at >= :since AND r.prompted_at <= :at
        AND r.status='answered' AND r.responded_at <= :at
      GROUP BY c.slug,m.is_internal,r.reason ORDER BY c.slug,m.is_internal,r.reason
    """),
                {"since": since, "at": at},
            )
        ).mappings()
    ]
    return {
        "report": "feedback",
        "since": since.isoformat(),
        "at": at.isoformat(),
        "rows": rows,
        "reasons": reasons,
        "denominator": "created offers, including unconfirmed delivery; not all inactive managers",
        "reason_for_silent_managers": None,
    }
