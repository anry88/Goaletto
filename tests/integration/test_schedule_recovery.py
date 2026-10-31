"""Real match energy, employment intervals and conditional schedule ownership."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.candidates import load_candidates, sign_candidate
from goaletto.application.finances import close_salary
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.recovery import energy_status, recover_player
from goaletto.application.schedule import load_schedule
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.recovery import daily_recovery, recover
from goaletto.infrastructure.models import (
    Fixture,
    FixturePlan,
    LineupSlot,
    Match,
    Person,
    Player,
    PlayerRecovery,
    SalaryContract,
    StaffQualification,
)
from tests.integration.test_agent_wave import NOW, job, setup_world, start_fixture
from tests.integration.test_candidates import funded

pytestmark = pytest.mark.integration


async def test_real_final_energy_survives_next_read_and_no_recovery_during_outage(sessions):
    _, actor, club = await setup_world(sessions)
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where(
                Fixture.competition == "league",
                (Fixture.home_club_id == club) | (Fixture.away_club_id == club),
            )
            .order_by(Fixture.scheduled_start_at)
            .limit(1)
        )
        keeper = await session.scalar(
            select(LineupSlot.person_id).where(
                LineupSlot.club_id == fixture.home_club_id, LineupSlot.slot == 1
            )
        )
        condition = await recover_player(session, keeper, fixture.lineup_lock_at)
        condition.energy_bps = 5000
    fixture_id, start = await start_fixture(sessions, club)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        state = restore_state(match.state)
        player_id = state.home.lineup[0].player_id
        assert next(c for c in state.home.conditions if c.player_id == player_id).energy_bps == 5000
        row = await session.get(PlayerRecovery, player_id)
        assert row.active_match_id == match.id
        before = row.recovered_at
        await recover_player(session, player_id, start + timedelta(hours=48))
        assert row.recovered_at == before  # A paused/stalled game never heals via UI reads.
    for tick in range(1, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture_id)
        await job(sessions, "match.tick", fixture_id, tick)
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        state = restore_state(match.state)
        expected = next(c for c in state.home.conditions if c.player_id == player_id).energy_bps
        row = await session.get(PlayerRecovery, player_id)
        assert row.energy_bps == expected < 10000 and row.active_match_id is None
        frozen = state_hash(restore_state(match.starting_snapshot))
        first = await energy_status(
            session, player_id, fixture.actual_finish_at + timedelta(hours=1)
        )
        player = await session.get(Player, player_id)
        person = await session.get(Person, player_id)
        assert (
            first.bps
            == recover(expected, 0, 3600, daily_recovery(player.stamina, person.age, 0))[0]
        )
        assert first.bps < 10000 and first.full_at > fixture.actual_finish_at + timedelta(hours=1)
        assert (
            await energy_status(session, player_id, fixture.actual_finish_at + timedelta(hours=1))
        ).bps == first.bps
        assert state_hash(restore_state(match.starting_snapshot)) == frozen
        from goaletto.bot.schedule_views import schedule_view

        screen = await load_schedule(
            session,
            replace(
                actor, operation_id=uuid4(), at=fixture.actual_finish_at + timedelta(minutes=5)
            ),
        )
        entry = next(e for day in screen.days for e in day.entries if e.id == fixture_id)
        assert entry.status == "finished" and "✅ Матч завершён" in schedule_view(screen)[0]
        next_day = fixture.actual_finish_at.replace(hour=0, minute=0, second=0, microsecond=0)
        screen = await load_schedule(
            session, replace(actor, operation_id=uuid4(), at=next_day + timedelta(days=1))
        )
        assert all(e.id != fixture_id for day in screen.days for e in day.entries)


async def test_physio_qualification_is_pinned_to_employment_not_retroactive(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        player = await session.scalar(select(Player).where(Player.club_id == club).limit(1))
        person = await session.get(Person, player.person_id)
        player.stamina, person.age = 9, 27
        row = await recover_player(session, person.id, NOW)
        row.energy_bps = 5000
        screen = await load_candidates(session, replace(actor, operation_id=uuid4()), "staff")
        item = next(i for i in screen.items if i.offer.profile == "physiotherapist")
        q = await session.get(StaffQualification, (item.person.id, "physiotherapist"))
        skill = q.skill
        await sign_candidate(
            session,
            replace(actor, operation_id=uuid4()),
            screen.ownership_id,
            item.person.id,
            item.offer.fee,
            at=NOW + timedelta(hours=12),
        )
        contract = await session.scalar(
            select(SalaryContract).where(SalaryContract.person_id == item.person.id)
        )
        assert contract.recovery_skill == skill
        q.skill = 20 if skill != 20 else 1
        await close_salary(session, item.person.id, NOW + timedelta(hours=18))
        row = await recover_player(session, person.id, NOW + timedelta(hours=24))
        assert row.energy_bps == 6800 + 25 * skill
        old = row.energy_bps
        assert (await recover_player(session, person.id, NOW)).energy_bps == old


async def test_calendar_conditional_paths_timezone_activity_and_stale_owner(sessions):
    result, actor, club = await setup_world(sessions)
    actor = replace(actor, operation_id=uuid4())
    async with sessions.begin() as session:
        plan = FixturePlan(
            id=uuid4(),
            season_id=result.season_id,
            competition="cup",
            round_number=2,
            conditional_ref="winner:tie",
            possible_club_ids=[str(club)],
            assigned_day=NOW.date() + timedelta(days=2),
            leg=2,
            neutral=True,
            stage="Semifinal",
        )
        session.add(plan)
        await session.flush()
        screen = await load_schedule(session, actor)
        conditional = [e for d in screen.days for e in d.entries if e.id == plan.id]
        assert len(conditional) == 1 and conditional[0].kickoff is None
        assert conditional[0].conditional and conditional[0].venue == "neutral"
        assert (
            len(
                (
                    await load_schedule(
                        session, replace(actor, operation_id=uuid4()), competition="cup"
                    )
                ).days
            )
            == 1
        )
        with pytest.raises(PermissionDenied):
            await load_schedule(session, actor, ownership_id=uuid4())


async def test_calendar_and_energy_through_real_dispatcher_after_commit(sessions):
    from datetime import UTC, datetime

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from goaletto.bot.main import build_dispatcher
    from goaletto.bot.result_views import compact_id
    from goaletto.infrastructure.models import Manager
    from tests.integration.test_game_dispatcher import Source, Telegram
    from tests.unit.test_language import assert_english

    _, actor, _ = await setup_world(sessions)
    source = Source(sessions)
    transport = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    number = 100

    async def send(command=None, data=None):
        nonlocal number
        number += 1
        if command:
            update = Update(
                update_id=number,
                message=Message(
                    message_id=number,
                    date=datetime.now(UTC),
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=command,
                ),
            )
        else:
            update = Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"calendar-{number}",
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        await dispatcher.feed_update(bot, update)
        return transport.last_message

    screen = await send("/calendar")
    assert "Ближайшие 10 дней" in screen.text and "UTC" in screen.text
    next_page = next(
        b.callback_data
        for row in screen.reply_markup.inline_keyboard
        for b in row
        if (b.callback_data or "").endswith(":2")
    )
    assert "Страница 2/3" in (await send(data=next_page)).text
    assert "⚡" in (await send("/lineup")).text
    async with sessions.begin() as session:
        (await session.get(Manager, actor.manager_id)).locale = "en"
    screen = await send("/calendar")
    assert_english((screen.text, screen.reply_markup))
    stale = await send(data=f"ca:list:{compact_id(uuid4())}:1")
    assert "expired" in stale.text
    await bot.session.close()
