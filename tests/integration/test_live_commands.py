"""Real PG admission, reservations, deadlines and tick/halftime application."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.game_actions import PermissionDenied
from goaletto.application.live_commands import (
    CommandUnavailable,
    accept_live_command,
    load_live_screen,
)
from goaletto.domain.match_codec import restore_state
from goaletto.infrastructure.models import AcceptedCommand, Club, Match, OwnershipPeriod
from tests.integration.test_agent_wave import job, setup_world, start_fixture

pytestmark = pytest.mark.integration


async def live(sessions):
    _, actor, club_id = await setup_world(sessions)
    fixture_id, at = await start_fixture(sessions, club_id)
    actor = replace(actor, at=at)
    async with sessions.begin() as session:
        screen = await load_live_screen(session, actor, clock=lambda: at, record=False)
    return actor, fixture_id, at, screen


async def accept(sessions, actor, screen, kind, payload, update=100, at=None):
    now = at or actor.at
    actor = replace(actor, operation_id=uuid4(), update_id=update, at=now)
    async with sessions.begin() as session:
        receipt = await accept_live_command(
            session,
            actor,
            match_id=screen.match_id,
            ownership_id=screen.ownership_id,
            target=screen.target,
            halftime=screen.halftime,
            kind=kind,
            payload=payload,
            clock=lambda: now,
        )
    return actor, receipt


async def test_accepted_command_retries_then_applies_once_with_real_update_id(sessions):
    actor, fixture_id, at, screen = await live(sessions)
    command_actor, first = await accept(
        sessions, actor, screen, "mentality", {"mentality": "attack"}
    )
    assert first.status == "accepted"
    async with sessions.begin() as session:
        again = await accept_live_command(
            session,
            command_actor,
            match_id=screen.match_id,
            ownership_id=screen.ownership_id,
            target=1,
            halftime=False,
            kind="mentality",
            payload={"mentality": "attack"},
            clock=lambda: at,
        )
        assert again.id == first.id
    await job(sessions, "match.tick", fixture_id, 1)
    async with sessions.begin() as session:
        row = await session.get(AcceptedCommand, first.id)
        assert row.status == "applied" and row.applied_tick_number == 1
        assert row.telegram_update_id == 100
        match = await session.get(Match, screen.match_id)
        team = (
            restore_state(match.state).home
            if screen.side.value == "home"
            else restore_state(match.state).away
        )
        assert team.mentality.value == "attack"
    await job(sessions, "match.tick", fixture_id, 1)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        assert restore_state(match.state).applied_commands.count(first.id) == 1


async def test_deadline_stale_ownership_pause_and_conflicting_receipt_are_rejected(sessions):
    actor, fixture_id, at, screen = await live(sessions)
    with pytest.raises(CommandUnavailable):
        await accept(
            sessions,
            actor,
            screen,
            "tactics",
            {"tactic": "pressing"},
            at=at + timedelta(seconds=60),
        )
    async with sessions.begin() as session:
        with pytest.raises(PermissionDenied):
            await accept_live_command(
                session,
                replace(actor, operation_id=uuid4(), update_id=101),
                match_id=screen.match_id,
                ownership_id=uuid4(),
                target=1,
                halftime=False,
                kind="tactics",
                payload={"tactic": "pressing"},
                clock=lambda: at,
            )
    command_actor, receipt = await accept(
        sessions, actor, screen, "mentality", {"mentality": "attack"}
    )
    async with sessions.begin() as session:
        with pytest.raises(PermissionDenied):
            await accept_live_command(
                session,
                command_actor,
                match_id=screen.match_id,
                ownership_id=screen.ownership_id,
                target=1,
                halftime=False,
                kind="mentality",
                payload={"mentality": "defence"},
                clock=lambda: at,
            )
    from goaletto.application.match_runtime import pause_match

    async with sessions.begin() as session:
        await pause_match(session, fixture_id, at + timedelta(seconds=1))
    with pytest.raises(CommandUnavailable):
        await accept(sessions, actor, screen, "tactics", {"tactic": "pressing"}, update=102)


async def test_pending_tactics_and_instructions_reserve_limits_before_tick(sessions):
    actor, fixture_id, at, screen = await live(sessions)
    for update, tactic in enumerate(("pressing", "counter", "wings"), 100):
        await accept(sessions, actor, screen, "tactics", {"tactic": tactic}, update)
    with pytest.raises(CommandUnavailable, match="tactic_limit"):
        await accept(sessions, actor, screen, "tactics", {"tactic": "defence"}, 103)
    team = screen.state.home if screen.side.value == "home" else screen.state.away
    person = next(s.player_id for s in team.lineup if s.position != "GK")
    for update, value in enumerate(("move_left", "move_right", "drop_back"), 104):
        await accept(
            sessions,
            actor,
            screen,
            "instruction",
            {"player_id": str(person), "instruction": value},
            update,
        )
    with pytest.raises(CommandUnavailable, match="instruction_limit"):
        await accept(
            sessions,
            actor,
            screen,
            "instruction",
            {"player_id": str(person), "instruction": "runs_behind"},
            107,
        )
    await job(sessions, "match.tick", fixture_id, 1)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        state = restore_state(match.state)
        team = state.home if screen.side.value == "home" else state.away
        assert (team.tactic_changes, team.instruction_changes) == (3, 3)
        assert team.instructions[0].instruction.value == "drop_back"


async def test_concurrent_duplicate_substitution_and_expired_owner_at_application(sessions):
    actor, fixture_id, at, screen = await live(sessions)
    team = screen.state.home if screen.side.value == "home" else screen.state.away
    outgoing = team.lineup[2].player_id
    incoming = next(
        p for p in team.bench if not next(c for c in team.conditions if c.player_id == p).injured
    )
    payload = {"outgoing_id": str(outgoing), "incoming_id": str(incoming)}

    async def submit(update):
        try:
            return await accept(sessions, actor, screen, "substitution", payload, update)
        except CommandUnavailable:
            return None

    values = await asyncio.wait_for(asyncio.gather(submit(100), submit(101)), timeout=15)
    assert sum(v is not None for v in values) == 1
    first = next(v[1] for v in values if v is not None)
    async with sessions.begin() as session:
        ownership = await session.get(OwnershipPeriod, screen.ownership_id)
        ownership.ended_at = at + timedelta(seconds=30)
        ownership.end_reason = "inactive"
        club = await session.get(Club, screen.club_id)
        club.owner_telegram_id = None
    await job(sessions, "match.tick", fixture_id, 1)
    async with sessions.begin() as session:
        row = await session.get(AcceptedCommand, first.id)
        assert row.status == "cancelled"
        match = await session.get(Match, screen.match_id)
        state = restore_state(match.state)
        assert first.id not in state.applied_commands


async def test_halftime_substitution_applies_in_free_window_and_deadline_after_wait(sessions):
    actor, fixture_id, at, screen = await live(sessions)
    for tick in range(1, 16):
        await job(sessions, "match.tick", fixture_id, tick)
    now = at + timedelta(minutes=16)
    actor = replace(actor, at=now)
    async with sessions.begin() as session:
        screen = await load_live_screen(session, actor, record=False, clock=lambda: now)
    assert screen.halftime and screen.target == 16
    team = screen.state.home if screen.side.value == "home" else screen.state.away
    outgoing = team.lineup[0].player_id
    incoming = next(
        p for p in team.bench if not next(c for c in team.conditions if c.player_id == p).injured
    )
    _, receipt = await accept(
        sessions,
        actor,
        screen,
        "substitution",
        {"outgoing_id": str(outgoing), "incoming_id": str(incoming)},
    )
    times = iter((now, screen.deadline))
    async with sessions.begin() as session:
        with pytest.raises(CommandUnavailable, match="waiting"):
            await accept_live_command(
                session,
                replace(actor, update_id=102, operation_id=uuid4()),
                match_id=screen.match_id,
                ownership_id=screen.ownership_id,
                target=16,
                halftime=True,
                kind="mentality",
                payload={"mentality": "attack"},
                clock=lambda: next(times),
            )
    await job(sessions, "match.second_half_start", fixture_id)
    async with sessions.begin() as session:
        row = await session.get(AcceptedCommand, receipt.id)
        assert row.status == "applied" and row.applied_tick_number is None
        match = await session.get(Match, screen.match_id)
        state = restore_state(match.state)
        team = state.home if screen.side.value == "home" else state.away
        assert team.substitution_windows == ()


async def test_real_dispatcher_live_controls_ack_before_commit_and_show_applied(
    sessions, monkeypatch
):
    from datetime import datetime

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from goaletto.bot import live_router
    from goaletto.bot.live_views import callback
    from goaletto.bot.main import build_dispatcher
    from tests.integration.test_game_dispatcher import Source, Telegram

    actor, fixture_id, at, screen = await live(sessions)

    class Time(datetime):
        @classmethod
        def now(cls, tz=None):
            return at

    monkeypatch.setattr(live_router, "datetime", Time)
    source = Source(sessions)
    telegram = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=telegram)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    message = Message(
        message_id=77, date=at, chat=Chat(id=1001, type="private"), from_user=user, text="/match"
    )
    await dispatcher.feed_update(bot, Update(update_id=100, message=message))
    assert "Дедлайн" in telegram.last_message.text
    data = callback(screen, "m", 1)
    query = CallbackQuery(
        id="live-dispatch-101",
        from_user=user,
        chat_instance="fake",
        message=telegram.last_message,
        data=data,
    )
    await dispatcher.feed_update(bot, Update(update_id=101, callback_query=query))
    assert "Принято" in telegram.last_message.text and "Применено" not in telegram.last_message.text
    from aiogram.methods import AnswerCallbackQuery

    assert isinstance(telegram.calls[-2], AnswerCallbackQuery)
    await job(sessions, "match.tick", fixture_id, 1)
    query = query.model_copy(
        update={"id": "live-refresh-102", "data": "lv:open", "message": telegram.last_message}
    )
    await dispatcher.feed_update(bot, Update(update_id=102, callback_query=query))
    assert "Применено" in telegram.last_message.text
    # A button from the previous window cannot queue another command.
    query = query.model_copy(
        update={"id": "live-stale-103", "data": data, "message": telegram.last_message}
    )
    await dispatcher.feed_update(bot, Update(update_id=103, callback_query=query))
    assert "устарело" in telegram.last_message.text
    async with sessions.begin() as session:
        assert (
            len(
                (
                    await session.scalars(
                        select(AcceptedCommand).where(AcceptedCommand.match_id == screen.match_id)
                    )
                ).all()
            )
            == 1
        )
    await bot.session.close()


async def test_fresh_injury_guard_cannot_be_bypassed_with_uuid_hex_payload(sessions):
    from goaletto.infrastructure.models import Player

    actor, _, at, screen = await live(sessions)
    team = screen.state.home if screen.side.value == "home" else screen.state.away
    incoming = team.bench[0]
    async with sessions.begin() as session:
        player = await session.get(Player, incoming)
        player.injured_until = at + timedelta(hours=1)
    with pytest.raises(CommandUnavailable, match="injured"):
        await accept(
            sessions,
            actor,
            screen,
            "substitution",
            {
                "outgoing_id": team.lineup[2].player_id.hex,
                "incoming_id": incoming.hex,
            },
        )
