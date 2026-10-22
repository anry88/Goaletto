"""Hand-count SQL acceptance in temp tables on the existing local application DB.

One rolled-back transaction; no database creation, reset or persistent game writes.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from goaletto.config import Settings
from goaletto.infrastructure.audience import audience_families

pytestmark = pytest.mark.integration


async def test_audience_distinguishes_registration_from_activity_and_preserves_exclusions():
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    assert settings.environment == "development" and url.database == "goaletto"
    assert url.host in {"127.0.0.1", "localhost"}
    engine = create_async_engine(url, hide_parameters=True)
    at = datetime(2026, 10, 22, 12, tzinfo=UTC)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                for table, columns in {
                    "managers": "id uuid,is_internal bool,first_game_start_at timestamptz",
                    "analytics_managers": "id uuid,manager_id uuid",
                    "acquisition_first_touches": (
                        "analytics_manager_id uuid,kind text,"
                        "campaign_id uuid,started_at timestamptz"
                    ),
                    "acquisition_campaigns": "id uuid,code text,created_at timestamptz",
                    "analytics_exclusions": "manager_id uuid",
                    "journey_events": (
                        "analytics_manager_id uuid,version int,origin text,"
                        "user_activity bool,occurred_at timestamptz,recorded_at timestamptz"
                    ),
                    "ownership_periods": "manager_id uuid,claimed_at timestamptz",
                }.items():
                    await connection.execute(
                        text(f"CREATE TEMP TABLE {table} ({columns}) ON COMMIT DROP")
                    )
                campaign = uuid4()
                await connection.execute(
                    text("INSERT INTO acquisition_campaigns VALUES(:id,'PRIVATE_source',:at)"),
                    {"id": campaign, "at": at},
                )
                for (
                    _name,
                    days,
                    kind,
                    internal,
                    optout,
                    active_hours,
                    user,
                    recorded_future,
                    claimed,
                ) in (
                    ("old", 40, "direct", False, False, 2, True, False, True),
                    ("new", 1 / 24, "known", False, False, 0.1, True, False, True),
                    ("return", 2, "known", False, False, 48, True, False, False),
                    ("automatic", 10, "unknown", False, False, 0, False, False, False),
                    ("excluded", 0, "known", False, True, 0, True, False, False),
                    ("internal", 0, "known", True, False, 0, True, False, False),
                    ("late", 5, "unknown", False, False, 0, True, True, False),
                ):
                    manager, analytics = uuid4(), uuid4()
                    started = at - timedelta(days=days)
                    await connection.execute(
                        text("INSERT INTO managers VALUES(:m,:internal,:at)"),
                        {"m": manager, "internal": internal, "at": started},
                    )
                    await connection.execute(
                        text("INSERT INTO analytics_managers VALUES(:a,:m)"),
                        {"a": analytics, "m": manager},
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO acquisition_first_touches VALUES(:a,:kind,:campaign,:at)"
                        ),
                        {
                            "a": analytics,
                            "kind": kind,
                            "campaign": campaign if kind == "known" else None,
                            "at": started,
                        },
                    )
                    if optout:
                        await connection.execute(
                            text("INSERT INTO analytics_exclusions VALUES(:m)"), {"m": manager}
                        )
                    for _ in range(2):  # Repeated actions still count as one active manager.
                        await connection.execute(
                            text(
                                "INSERT INTO journey_events "
                                "VALUES(:a,1,:origin,:user,:at,:recorded)"
                            ),
                            {
                                "a": analytics,
                                "origin": "user" if user else "system",
                                "user": user,
                                "at": at - timedelta(hours=active_hours),
                                "recorded": at + timedelta(hours=1) if recorded_future else at,
                            },
                        )
                    if claimed:
                        await connection.execute(
                            text("INSERT INTO ownership_periods VALUES(:m,:at)"),
                            {"m": manager, "at": started},
                        )
                session = AsyncSession(bind=connection)
                families = await audience_families(session, at)
                samples = families[0].samples

                def total(window, measure):
                    return sum(
                        s.value
                        for s in samples
                        if s.labels["window"] == window and s.labels["measure"] == measure
                    )

                assert [total(w, "registered") for w in ("all", "1d", "7d", "30d")] == [5, 1, 3, 4]
                assert [total(w, "active") for w in ("1d", "7d", "30d")] == [2, 3, 3]
                assert total("all", "claimed") == 2
                assert not any("PRIVATE" in str(s.labels) for f in families for s in f.samples)
                assert (
                    sum(
                        s.value
                        for s in samples
                        if s.labels == {"window": "7d", "source": "c01", "measure": "active"}
                    )
                    == 2
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
