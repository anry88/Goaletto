"""Read-only audience counts. Source text never becomes a Prometheus label."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from prometheus_client.core import GaugeMetricFamily, Metric
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc

SOURCE_LIMIT = 32
WINDOWS = ("all", "1d", "7d", "30d")
MEASURES = ("registered", "active", "claimed")


@dataclass(frozen=True)
class SourceLabel:
    bucket: str
    code: str
    campaign_id: str


async def source_labels(session: AsyncSession) -> tuple[SourceLabel, ...]:
    rows = (
        await session.execute(
            text("SELECT id,code FROM acquisition_campaigns ORDER BY created_at,id LIMIT 32")
        )
    ).mappings()
    return tuple(
        SourceLabel(f"c{i:02}", row["code"], str(row["id"])) for i, row in enumerate(rows, 1)
    )


def mapping_token(labels: tuple[SourceLabel, ...]) -> int:
    # A value, never a label: exact within an IEEE-754 integer. Guards history
    # against a reordered mapping (for example a backdated operator campaign).
    encoded = json.dumps([(x.bucket, x.code, x.campaign_id) for x in labels]).encode()
    return int(hashlib.sha256(encoded).hexdigest()[:12], 16)


AUDIENCE_SQL = """
WITH people AS (
 SELECT m.id,am.id AS analytics_id,coalesce(a.started_at,m.first_game_start_at) AS registered_at,
 CASE WHEN a.kind='direct' THEN 'direct'
      WHEN a.kind='known' THEN coalesce(CAST(:sources AS jsonb)->>a.campaign_id::text,'other')
      ELSE 'unknown' END AS source
 FROM managers m LEFT JOIN analytics_managers am ON am.manager_id=m.id
 LEFT JOIN acquisition_first_touches a ON a.analytics_manager_id=am.id
 WHERE NOT m.is_internal AND m.first_game_start_at<=:at
 AND NOT EXISTS(SELECT 1 FROM analytics_exclusions x WHERE x.manager_id=m.id)
), activity AS (
 SELECT e.analytics_manager_id,max(e.occurred_at) AS last_at
 FROM journey_events e JOIN people p ON p.analytics_id=e.analytics_manager_id
 WHERE e.version=1 AND e.origin='user' AND e.user_activity
 AND e.occurred_at>=p.registered_at AND e.occurred_at<=:at AND e.recorded_at<=:at
 AND e.occurred_at>=CAST(:at AS timestamptz)-INTERVAL '30 days'
 GROUP BY e.analytics_manager_id
), windows(name,since) AS (VALUES
 ('all',NULL::timestamptz),('1d',CAST(:at AS timestamptz)-INTERVAL '1 day'),
 ('7d',CAST(:at AS timestamptz)-INTERVAL '7 days'),
 ('30d',CAST(:at AS timestamptz)-INTERVAL '30 days'))
SELECT w.name AS window,p.source,
 count(*) FILTER(WHERE w.since IS NULL OR p.registered_at>=w.since) AS registered,
 count(*) FILTER(WHERE w.since IS NOT NULL AND a.last_at>=w.since) AS active,
 count(*) FILTER(WHERE (w.since IS NULL OR p.registered_at>=w.since) AND EXISTS(
  SELECT 1 FROM ownership_periods o WHERE o.manager_id=p.id AND o.claimed_at<=:at
 )) AS claimed
FROM people p CROSS JOIN windows w LEFT JOIN activity a ON a.analytics_manager_id=p.analytics_id
GROUP BY w.name,p.source
"""


async def audience_families(session: AsyncSession, at: datetime) -> tuple[Metric, ...]:
    require_utc(at)
    labels = await source_labels(session)
    rows = (
        await session.execute(
            text(AUDIENCE_SQL),
            {
                "at": at,
                "sources": json.dumps({x.campaign_id: x.bucket for x in labels}),
            },
        )
    ).mappings()
    values = {(r["window"], r["source"], m): int(r[m]) for r in rows for m in MEASURES}
    family = GaugeMetricFamily(
        "goaletto_audience",
        "Current audience snapshot, not an event counter.",
        labels=["window", "source", "measure"],
    )
    for window in WINDOWS:
        for source in ("direct", "unknown", "other", *(x.bucket for x in labels)):
            for measure in MEASURES:
                if measure == "active" and window == "all":
                    continue
                family.add_metric(
                    [window, source, measure], values.get((window, source, measure), 0)
                )
    token = GaugeMetricFamily(
        "goaletto_audience_source_map",
        "Source mapping fingerprint (value only).",
        value=mapping_token(labels),
    )
    return family, token
