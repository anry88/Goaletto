"""25-person roster selection and full real worker match on the single app DB."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from goaletto.application.finances import close_salary, salary_contract
from goaletto.application.lineup import LineupInput, LineupStale, load_lineup, save_lineup
from goaletto.bot.lineup_handlers import _dispatch, build_callback_response
from goaletto.bot.result_views import compact_id
from goaletto.domain.lineup import StarterPick
from goaletto.domain.match_codec import restore_state
from goaletto.infrastructure.models import Club, LineupSlot, Match, Player
from tests.integration.test_agent_wave import NOW, job, setup_world, start_fixture

pytestmark = pytest.mark.integration


async def roster25(session, club_id):
    # Five existing, non-warned people from five other clubs. No new identities/art.
    sources = (
        await session.scalars(select(Club.id).where(Club.id != club_id).order_by(Club.id).limit(5))
    ).all()
    for source in sources:
        player = await session.scalar(
            select(Player)
            .join(
                LineupSlot,
                (LineupSlot.person_id == Player.person_id) & (LineupSlot.club_id == Player.club_id),
            )
            .where(Player.club_id == source, Player.status == "active", LineupSlot.slot >= 12)
            .order_by(LineupSlot.slot.desc())
            .limit(1)
        )
        slots = (
            await session.scalars(
                select(LineupSlot).where(LineupSlot.club_id == source).order_by(LineupSlot.slot)
            )
        ).all()
        await close_salary(session, player.person_id, NOW)
        await session.execute(delete(LineupSlot).where(LineupSlot.club_id == source))
        await session.flush()
        keep = [slot for slot in slots if slot.person_id != player.person_id]
        player.club_id = club_id
        await session.flush()
        session.add_all(
            [
                LineupSlot(club_id=source, slot=i, person_id=slot.person_id, position=slot.position)
                for i, slot in enumerate(keep, 1)
            ]
        )
        await salary_contract(session, player.person_id, club_id, "player", NOW)


async def test_25_players_choose_reserve_and_full_match_keeps_only_selected_people(sessions):
    _, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        await roster25(session, club_id)
        data = await load_lineup(session, actor)
        assert (len(data.starters), len(data.bench), len(data.reserves)) == (11, 9, 5)
        incoming = data.reserves[0].person_id
        outgoing = data.starters[-1].person_id
        captain = data.starters[0].person_id
        picks = tuple(
            StarterPick(incoming if i == 10 else p.person_id, p.slot_position)
            for i, p in enumerate(data.starters)
        )
        await save_lineup(
            session,
            replace(actor, operation_id=uuid4(), update_id=100),
            LineupInput(
                picks,
                tuple(p.person_id for p in data.bench),
                data.formation,
                captain,
                data.tactic,
                data.mentality,
                data.version,
            ),
            clock=lambda: NOW,
        )
        fresh = await load_lineup(session, actor)
        assert incoming in {p.person_id for p in fresh.starters} and outgoing in {
            p.person_id for p in fresh.reserves
        }
        assert len(fresh.reserves) == 5
        with pytest.raises(LineupStale):
            await save_lineup(
                session,
                replace(actor, operation_id=uuid4(), update_id=101),
                LineupInput(
                    picks,
                    tuple(p.person_id for p in data.bench),
                    data.formation,
                    captain,
                    data.tactic,
                    data.mentality,
                    data.version,
                ),
                clock=lambda: NOW,
            )
    fixture, _ = await start_fixture(sessions, club_id)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        state = restore_state(match.starting_snapshot)
        team = state.home if state.home.snapshot.club_id == club_id else state.away
        assert len(team.snapshot.players) == 20 and incoming in {
            p.id for p in team.snapshot.players
        }
        assert outgoing not in {p.id for p in team.snapshot.players}
    for tick in range(1, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture)
        await job(sessions, "match.tick", fixture, tick)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        assert match.phase == "full_time" and match.completed_tick == 30


async def test_owned_bench_callbacks_drop_replace_add_without_overflow(sessions, monkeypatch):
    # Callback saves recheck the real clock; this scenario owns an explicit past clock.
    async def save_at_scenario_time(*args, **kwargs):
        return await save_lineup(*args, **kwargs, clock=lambda: NOW)

    monkeypatch.setattr("goaletto.bot.lineup_handlers.save_lineup", save_at_scenario_time)
    _, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        await roster25(session, club_id)
        data = await load_lineup(session, actor)
        removed = data.bench[0].person_id
        await _dispatch(
            session, replace(actor, operation_id=uuid4(), update_id=100), data, "br", ("12",)
        )
        data = await load_lineup(session, actor)
        assert len(data.bench) == 8 and removed in {p.person_id for p in data.reserves}
        incoming = next(p.person_id for p in data.reserves if p.person_id != removed)
        text, _ = await build_callback_response(
            session,
            actor.telegram_id,
            "bench-add-test",
            f"ln:b:20:{compact_id(incoming)}:{compact_id(data.ownership_id)}:{data.version}",
            NOW,
            update_id=101,
        )
        assert "Заявка: 20/25" in text
        data = await load_lineup(session, actor)
        assert (
            len(data.bench) == 9
            and len(data.reserves) == 5
            and data.bench[-1].person_id == incoming
        )
        with pytest.raises(ValueError):
            await _dispatch(
                session,
                replace(actor, operation_id=uuid4(), update_id=102),
                data,
                "bs",
                ("21", compact_id(removed)),
            )
        assert len((await load_lineup(session, actor)).bench) == 9
