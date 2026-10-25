"""Audience contracts, atomic match gains, historical billing and compact views."""

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.finances import cycle_finances
from goaletto.application.history import get_match_report
from goaletto.application.results import finalize_result
from goaletto.application.stadium import crowd_for_fixture, load_stadium
from goaletto.application.supporters import apply_season, merchandise_for_period, supporters_at
from goaletto.bot.history_cards import report_view
from goaletto.bot.stadium_views import stadium_view
from goaletto.domain.supporters import match_gain
from goaletto.infrastructure.models import (
    Club,
    FinanceCycle,
    Fixture,
    Job,
    Match,
    MatchCrowd,
    SupporterChange,
)
from goaletto.presentation.i18n import using_locale
from tests.integration.test_agent_wave import NOW, start_fixture
from tests.integration.test_candidates import action, funded
from tests.integration.test_match_practice_runtime import completed_checkpoint

pytestmark = pytest.mark.integration


async def test_real_match_freezes_fan_terms_grants_both_clubs_once_and_report_is_own(sessions):
    _, actor, club_id = await funded(sessions)
    fixture_id, at = await start_fixture(sessions, club_id)
    final = await completed_checkpoint(sessions, fixture_id, at)
    async with sessions.begin() as session:
        crowd = await session.get(MatchCrowd, final.match_id)
        assert crowd.policy_version == "stadium-demand-v2"
        terms = crowd.basis["supporter_terms"]
        assert terms["policy"] == "supporters-v1" and set(terms["clubs"]) == {
            str(final.home.snapshot.club_id),
            str(final.away.snapshot.club_id),
        }
        assert await finalize_result(session, fixture_id, at + timedelta(hours=1))
        for team, opp in ((final.home, final.away), (final.away, final.home)):
            key = team.snapshot.club_id
            player_club = await session.get(Club, key)
            frozen = terms["clubs"][str(key)]
            gain = match_gain(
                frozen["supporters"],
                frozen["tier"],
                terms["clubs"][str(opp.snapshot.club_id)]["tier"],
                "league",
                "league",
                team.stats.goals,
                opp.stats.goals,
            )
            assert player_club.supporters == frozen["supporters"] + gain
        assert await session.scalar(select(func.count()).select_from(SupporterChange)) == 2
        report = await get_match_report(session, action(actor), fixture_id)
        row = await session.scalar(
            select(SupporterChange).where(SupporterChange.club_id == club_id)
        )
        assert report.supporters_gain == row.after_count - row.before_count
        if report.supporters_gain:
            assert "👥 Болельщики: +" in report_view(report)[0]
        saved = (await session.get(Club, club_id)).supporters
        assert not await finalize_result(session, fixture_id, at + timedelta(hours=2))
        assert (await session.get(Club, club_id)).supporters == saved
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(text("UPDATE supporter_changes SET after_count=after_count+1"))


async def test_failure_after_fan_update_rolls_back_result_and_audience(sessions, monkeypatch):
    import goaletto.application.supporters as runtime

    _, _, club_id = await funded(sessions)
    fixture_id, at = await start_fixture(sessions, club_id)
    final = await completed_checkpoint(sessions, fixture_id, at)
    original = runtime._save_change

    async def fail(*args, **kw):
        await original(*args, **kw)
        raise RuntimeError("after audience")

    with monkeypatch.context() as patched:
        patched.setattr(runtime, "_save_change", fail)
        with pytest.raises(RuntimeError, match="after audience"):
            async with sessions.begin() as session:
                await finalize_result(session, fixture_id, at + timedelta(hours=1))
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(SupporterChange)) == 0
        assert (await session.get(Club, club_id)).supporters == 2500
        assert await finalize_result(session, fixture_id, at + timedelta(hours=2))
        assert await session.scalar(select(func.count()).select_from(SupporterChange)) == 2
        assert (await session.get(Match, final.match_id)).state == final_to_document(final)


def final_to_document(state):
    from goaletto.domain.match_codec import state_document

    return state_document(state)


async def test_match_without_pinned_audience_never_gets_retroactive_growth(sessions, monkeypatch):
    import goaletto.application.supporters as runtime

    _, _, club_id = await funded(sessions)

    async def no_terms(*args, **kw):
        return None

    with monkeypatch.context() as old:
        old.setattr(runtime, "frozen_terms", no_terms)
        fixture_id, at = await start_fixture(sessions, club_id)
    await completed_checkpoint(sessions, fixture_id, at)
    async with sessions.begin() as session:
        await finalize_result(session, fixture_id, at + timedelta(hours=1))
        assert await session.scalar(select(func.count()).select_from(SupporterChange)) == 0
        assert (await session.get(Club, club_id)).supporters == 2500


async def test_achievement_once_and_late_merchandise_bill_uses_historical_fans(sessions):
    world, _, club_id = await funded(sessions)
    async with sessions.begin() as session:
        club = await session.get(Club, club_id)
        await apply_season(session, club, world.season_id, 5, 1, 4, NOW + timedelta(days=2))
        after = club.supporters
        assert after > 2500
        await apply_season(session, club, world.season_id, 5, 1, 4, NOW + timedelta(days=3))
        assert club.supporters == after
        assert await supporters_at(session, club, NOW + timedelta(days=1)) == 2500
        assert await supporters_at(session, club, NOW + timedelta(days=3)) == after
        income, periods = await merchandise_for_period(session, club, NOW, NOW + timedelta(days=4))
        assert [p["supporters"] for p in periods] == [2500, after]
        assert income == 1250 + (after + 1) // 2
        # Real invoice is processed late; it must retain those effective intervals.
        work = await session.scalar(
            select(Job)
            .where(Job.kind == "finance.cycle", Job.payload["club_id"].astext == str(club_id))
            .order_by(Job.due_at)
            .limit(1)
        )
        assert work is not None
        await cycle_finances(session, work, work.due_at + timedelta(days=10))
        invoice = await session.scalar(select(FinanceCycle).where(FinanceCycle.club_id == club_id))
        expected, rows = await merchandise_for_period(
            session,
            club,
            datetime.fromisoformat(work.payload["period_start"]),
            datetime.fromisoformat(work.payload["period_end"]),
        )
        assert invoice.snapshot["amounts"]["merchandise"] == expected
        assert invoice.snapshot["supporter_periods"] == rows


async def test_forecast_status_and_locked_crowd_short_ru_en(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where(Fixture.home_club_id == club_id)
            .order_by(Fixture.scheduled_start_at)
            .limit(1)
        )
        club = await session.get(Club, club_id)
        base = await crowd_for_fixture(session, fixture, club, NOW)
        # Projections leave persisted tournament plans untouched.
        friendly = SimpleNamespace(season_id=fixture.season_id, competition="friendly", stage=None)
        cup = SimpleNamespace(season_id=fixture.season_id, competition="cup", stage="semi")
        assert (
            (await crowd_for_fixture(session, friendly, club, NOW)).fans
            < base.fans
            < (await crowd_for_fixture(session, cup, club, NOW)).fans
        )
    fixture_id, at = await start_fixture(sessions, club_id)
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        # Pick the owner of the actual home side for the stadium lock view.
        if fixture.home_club_id == club_id:
            screen = await load_stadium(session, replace(action(actor), at=at), record=False)
            assert screen.locked is not None
            for lang in ("ru", "en"):
                with using_locale(lang):
                    body, _ = stadium_view(screen)
                assert len(body) < 3900 and "bps" not in body


async def test_final_notification_contains_only_owners_actual_audience_gain(sessions):
    from goaletto.application.outbox_delivery import render_text, valid_payload
    from goaletto.infrastructure.models import OutboxMessage
    from tests.integration.test_friendly import _play

    _, _, club_id = await funded(sessions)
    fixture_id, _ = await start_fixture(sessions, club_id)
    await _play(sessions, fixture_id)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        own = await session.scalar(
            select(SupporterChange).where(
                SupporterChange.club_id == club_id, SupporterChange.match_id == match.id
            )
        )
        message = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.payload["fixture_id"].astext == str(fixture_id),
                OutboxMessage.payload["kind"].astext == "match_result",
            )
        )
        assert message is not None and valid_payload(message.payload)
        gain = own.after_count - own.before_count
        assert message.payload.get("supporters_gain", 0) == gain
        body = render_text(message.payload)
        assert len(body) < 3900
        if gain:
            assert f"👥 Болельщики: +{gain}" in body
