"""Persisted injuries, conservative repair and current condition displays."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.club_view import get_player_card
from goaletto.application.injuries import injured_players, repair_match_injuries
from goaletto.application.lineup import load_lineup
from goaletto.application.recovery import recover_player
from goaletto.application.results import finalize_result
from goaletto.bot.club_cards import player_view
from goaletto.domain.match_codec import state_hash
from goaletto.domain.match_types import EnginePolicy
from goaletto.infrastructure.models import (
    Fixture,
    Match,
    MatchInjury,
    Player,
    PlayerRecovery,
)
from tests.integration.test_agent_wave import NOW, start_fixture
from tests.integration.test_candidates import action, funded
from tests.integration.test_match_practice_runtime import completed_checkpoint

pytestmark = pytest.mark.integration


def injury_policy(monkeypatch, *, forced=False):
    import goaletto.application.match_runtime as runtime

    monkeypatch.setattr(
        runtime,
        "EnginePolicy",
        lambda **kw: EnginePolicy(
            **kw, injury_bps=10000 if forced else 500, yellow_bps=0, direct_red_bps=0
        ),
    )


async def finished(sessions, monkeypatch):
    injury_policy(monkeypatch)
    _, actor, club = await funded(sessions)
    fixture, at = await start_fixture(sessions, club)
    final = await completed_checkpoint(sessions, fixture, at)
    assert final.phase.value == "full_time" and injured_players(final)
    return actor, club, fixture, at, final


async def test_match_injury_persists_seven_days_retry_does_not_extend_and_energy_does_not_heal(
    sessions, monkeypatch
):
    actor, club, fixture, at, final = await finished(sessions, monkeypatch)
    async with sessions.begin() as session:
        await finalize_result(session, fixture, at + timedelta(hours=1))
        rows = (await session.scalars(select(MatchInjury))).all()
        assert len(rows) == len(injured_players(final))
        for row in rows:
            player = await session.get(Player, row.person_id)
            assert row.started_at == at + timedelta(minutes=33)
            assert (
                player.injured_until == row.unavailable_until == row.started_at + timedelta(days=7)
            )
            await recover_player(session, row.person_id, at + timedelta(days=2))
            assert player.injured_until == row.unavailable_until
        own = [r for r in rows if r.club_id == club]
        assert own
        card = await get_player_card(
            session, replace(action(actor), at=at + timedelta(days=2)), own[0].person_id
        )
        assert card.injured and "Травмирован до" in player_view(card)[0]
        data = await load_lineup(session, replace(action(actor), at=at + timedelta(days=2)))
        assert all(
            m.injured
            for m in (*data.starters, *data.bench, *data.reserves)
            if m.person_id in {r.person_id for r in own}
        )
        assert not await finalize_result(session, fixture, at + timedelta(days=3))
        assert await session.scalar(select(func.count()).select_from(MatchInjury)) == len(rows)
        # Deadline is inclusive for availability; no special healing job is needed.
        card = await get_player_card(
            session, replace(action(actor), at=own[0].unavailable_until), own[0].person_id
        )
        assert not card.injured
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(text("UPDATE match_injuries SET days=8"))


async def test_injury_rollback_and_repair_have_same_original_deadline(sessions, monkeypatch):
    import goaletto.application.injuries as runtime

    actor, club, fixture, at, final = await finished(sessions, monkeypatch)
    real_apply = runtime._apply

    async def fail(*args, **kw):
        await real_apply(*args, **kw)
        raise RuntimeError("after injury")

    with monkeypatch.context() as patched:
        patched.setattr(runtime, "_apply", fail)
        with pytest.raises(RuntimeError, match="after injury"):
            async with sessions.begin() as session:
                await finalize_result(session, fixture, at + timedelta(hours=1))
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(MatchInjury)) == 0
        # Simulate the proven historical gap using the original no-injury finalizer.
        with monkeypatch.context() as old:
            from unittest.mock import AsyncMock

            old.setattr(runtime, "apply_match_injuries", AsyncMock())
            await finalize_result(session, fixture, at + timedelta(hours=1))
        ids = {pid for pid, (c, _) in injured_players(final).items() if c == club}
        pid = next(iter(ids))
        recovery = await session.get(PlayerRecovery, pid)
        recovery.active_match_id = final.match_id
        with pytest.raises(ValueError, match="locked"):
            await repair_match_injuries(
                session, final.match_id, club, ids, state_hash(final), at + timedelta(days=1)
            )
        recovery.active_match_id = None
        with pytest.raises(ValueError, match="expired"):
            await repair_match_injuries(
                session, final.match_id, club, ids, state_hash(final), at + timedelta(days=8)
            )
        player = await session.get(Player, pid)
        status = player.status
        player.status = "retired"
        with session.no_autoflush, pytest.raises(ValueError, match="career"):
            await repair_match_injuries(
                session, final.match_id, club, ids, state_hash(final), at + timedelta(days=1)
            )
        player.status = status
        repaired = await repair_match_injuries(
            session, final.match_id, club, ids, state_hash(final), at + timedelta(days=1)
        )
        assert repaired and all(r.source == "repair" for r in repaired)
        for row in repaired:
            assert (await session.get(Player, row.person_id)).injured_until == at + timedelta(
                minutes=33, days=7
            )
        again = await repair_match_injuries(
            session, final.match_id, club, ids, state_hash(final), at + timedelta(days=2)
        )
        assert [r.unavailable_until for r in again] == [r.unavailable_until for r in repaired]
        with pytest.raises(ValueError, match="evidence"):
            await repair_match_injuries(
                session, final.match_id, club, ids, "0" * 64, at + timedelta(days=2)
            )


async def test_abandoned_match_keeps_injuries_and_releases_energy_without_awarded_score(
    sessions, monkeypatch
):
    from goaletto.application.match_runtime import handle_calendar_job
    from goaletto.infrastructure.models import MatchResult
    from tests.integration.test_cup_runtime import next_job

    injury_policy(monkeypatch, forced=True)
    _, _, club = await funded(sessions)
    fixture, at = await start_fixture(sessions, club)
    async with sessions.begin() as session:
        while (work := await next_job(session, fixture)) is not None:
            await handle_calendar_job(session, work, work.due_at)
            work.status = "done"
            f = await session.get(Fixture, fixture)
            if f.status == "abandoned":
                break
        assert f.status == "abandoned"
        m = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        assert await session.get(MatchResult, m.id) is None
        assert await session.scalar(select(func.count()).select_from(MatchInjury)) > 0
        assert not await session.scalar(
            select(PlayerRecovery.person_id).where(PlayerRecovery.active_match_id == m.id).limit(1)
        )


async def test_historical_repair_rejects_later_actual_participation(sessions, monkeypatch):
    from unittest.mock import AsyncMock

    from tests.integration.test_agent_wave import job

    _, club, fixture, at, final = await finished(sessions, monkeypatch)
    async with sessions.begin() as session:
        with monkeypatch.context() as old:
            old.setattr("goaletto.application.injuries.apply_match_injuries", AsyncMock())
            await finalize_result(session, fixture, at + timedelta(hours=1))
        following = await session.scalar(
            select(Fixture)
            .where(
                Fixture.status == "scheduled",
                Fixture.competition == "league",
                (Fixture.home_club_id == club) | (Fixture.away_club_id == club),
                Fixture.scheduled_start_at > at,
            )
            .order_by(Fixture.scheduled_start_at)
            .limit(1)
        )
        next_id = following.id
        blockers = (
            await session.scalars(
                select(Fixture.id).where(
                    Fixture.status == "scheduled",
                    Fixture.competition == "league",
                    Fixture.scheduled_start_at < following.scheduled_start_at,
                    Fixture.home_club_id.in_((following.home_club_id, following.away_club_id))
                    | Fixture.away_club_id.in_((following.home_club_id, following.away_club_id)),
                )
            )
        ).all()
    monkeypatch.setattr(
        "goaletto.application.match_runtime.EnginePolicy",
        lambda **kw: EnginePolicy(**kw, injury_bps=0, yellow_bps=0, direct_red_bps=0),
    )
    for blocked in blockers:
        await job(sessions, "match.lock_lineup", blocked)
        blocked_at = await job(sessions, "match.start", blocked)
        await completed_checkpoint(sessions, blocked, blocked_at)
        async with sessions.begin() as session:
            await finalize_result(session, blocked, blocked_at + timedelta(hours=1))
    await job(sessions, "match.lock_lineup", next_id)
    next_at = await job(sessions, "match.start", next_id)
    await completed_checkpoint(sessions, next_id, next_at)
    async with sessions.begin() as session:
        await finalize_result(session, next_id, next_at + timedelta(hours=1))
        ids = {pid for pid, (c, _) in injured_players(final).items() if c == club}
        with pytest.raises(ValueError, match="later appearance"):
            await repair_match_injuries(
                session, final.match_id, club, ids, state_hash(final), next_at + timedelta(hours=1)
            )


async def test_current_lineup_refreshes_energy_and_form_at_each_read(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        player = await session.scalar(
            select(Player).where(Player.club_id == club).order_by(Player.person_id)
        )
        energy = await recover_player(session, player.person_id, NOW)
        energy.energy_bps = 5000
        player.form = 8
        player.form_activity_at = NOW - timedelta(days=7)
        player.form_idle_steps = 0
        first = await load_lineup(session, replace(action(actor), at=NOW + timedelta(hours=1)))
        member = next(
            p
            for p in (*first.starters, *first.bench, *first.reserves)
            if p.person_id == player.person_id
        )
        assert member.form == 6 and member.energy_bps > 5000
        second = await load_lineup(session, replace(action(actor), at=NOW + timedelta(hours=2)))
        updated = next(
            p
            for p in (*second.starters, *second.bench, *second.reserves)
            if p.person_id == player.person_id
        )
        assert updated.energy_bps > member.energy_bps and updated.form == member.form


async def test_future_suspension_preview_does_not_advance_current_form_clock(sessions):
    from goaletto.application.discipline import tournament_scope
    from goaletto.infrastructure.models import PlayerDiscipline

    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        data = await load_lineup(session, action(actor))
        pid = data.starters[0].person_id
        player = await session.get(Player, pid)
        player.form, player.form_activity_at, player.form_idle_steps = 8, NOW, 0
        fixture = await session.scalar(
            select(Fixture)
            .where(
                Fixture.status == "scheduled",
                (Fixture.home_club_id == club) | (Fixture.away_club_id == club),
            )
            .order_by(Fixture.scheduled_start_at, Fixture.id)
            .limit(1)
        )
        scope = await tournament_scope(session, fixture)
        session.add(PlayerDiscipline(scope=scope, person_id=pid, yellows=0, bans=1))
        await session.flush()
        again = await load_lineup(session, action(actor))
        assert player.form == 8 and player.form_idle_steps == 0
        assert (await session.get(PlayerRecovery, pid)).recovered_at == NOW
        assert all(
            p.form == 8
            for p in (*again.starters, *again.bench, *again.reserves)
            if p.person_id == pid
        )


async def test_injured_starter_preview_uses_healthy_reserve_without_changing_template(sessions):
    from goaletto.infrastructure.models import LineupSlot

    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        before = await load_lineup(session, action(actor))
        injured = before.starters[0].person_id
        player = await session.get(Player, injured)
        player.injured_until = NOW + timedelta(days=30)
        player.form, player.form_activity_at, player.form_idle_steps = 8, NOW, 0
        after = await load_lineup(session, action(actor))
        assert injured not in {p.person_id for p in after.starters}
        assert not any(p.injured for p in after.starters)
        assert player.form == 8 and player.form_idle_steps == 0
        assert (
            await session.scalar(
                select(LineupSlot.person_id).where(LineupSlot.club_id == club, LineupSlot.slot == 1)
            )
            == injured
        )
