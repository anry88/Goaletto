"""Real PostgreSQL checks for the integrated first agent wave; Telegram is fake."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import func, select

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import user_effect
from goaletto.application.history import get_match_report
from goaletto.application.lineup import (
    LineupInput,
    LineupLocked,
    LineupStale,
    load_lineup,
    save_lineup,
)
from goaletto.application.match_runtime import activate_season, handle_calendar_job
from goaletto.application.onboarding import ClubUnavailable, begin_game, claim_club
from goaletto.application.outbox_delivery import Outcome, SendResult, claim_outbox, finish_delivery
from goaletto.application.world import initialize_world
from goaletto.bot.portrait_cache import portrait_cache_key
from goaletto.config import Settings
from goaletto.diagnostics.checks import collect_report
from goaletto.diagnostics.queries import collect_snapshot
from goaletto.domain.lineup import StarterPick
from goaletto.domain.ownership import ownership_deadline
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import (
    AcceptedCommand,
    Club,
    Fixture,
    Job,
    Match,
    MatchResult,
    OutboxBoard,
    OwnershipPeriod,
    Person,
    Standing,
)
from goaletto.infrastructure.outbox_runner import process_one_delivery
from goaletto.infrastructure.portrait_files import load_file_id, store_file_id

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 17, 18, tzinfo=UTC)
OPTIONS = WorldOptions(
    key="integration-wave",
    seed=0,
    first_kickoff=KICKOFF,
    initial_budget=50000,
)


async def setup_world(sessions, *, options=OPTIONS, at=NOW):
    async with sessions.begin() as session:
        result = await initialize_world(session, options, initialized_at=at)
        await activate_season(session, result.season_id, at)
        entry = await begin_game(session, telegram_id=1001, update_id=1, payload=None, at=at)
        claim_actor = replace(entry.actor, operation_id=uuid4(), update_id=4)
        owner = await existing_ownership(session, claim_actor, entry.offers[0].id)
        club_id = owner.club_id
    return result, claim_actor, club_id


async def existing_ownership(session, actor, club_id):
    """Seed a pre-upgrade manager for tests of existing clubs and historical records."""
    club = await session.get(Club, club_id)
    owner = OwnershipPeriod(
        id=uuid5(actor.operation_id, "ownership"),
        operation_key=f"claim:v1:{actor.operation_id}",
        club_id=club_id,
        manager_id=actor.manager_id,
        claimed_at=actor.at,
        last_game_action_at=actor.at,
        expires_at=ownership_deadline(actor.at),
    )
    session.add(owner)
    club.owner_telegram_id = actor.telegram_id
    await session.flush()
    await user_effect(
        session, actor, "club_claimed", Surface.ONBOARDING, ownership=owner, club_id=club_id
    )
    return owner


async def job(sessions, kind, fixture_id=None, tick=None):
    async with sessions.begin() as session:
        query = select(Job).where(Job.kind == kind)
        if fixture_id is not None:
            query = query.where(Job.payload["fixture_id"].astext == str(fixture_id))
        if tick is not None:
            query = query.where(Job.payload["tick_number"].as_integer() == tick)
        row = await session.scalar(query.order_by(Job.due_at).limit(1))
        assert row is not None
        at = row.due_at
        await handle_calendar_job(session, row, at)
        row.status = "done"
    return at


async def start_fixture(sessions, club_id):
    await job(sessions, "season.open")
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where(
                Fixture.competition == "league",
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            )
            .order_by(Fixture.scheduled_start_at)
            .limit(1)
        )
        fixture_id = fixture.id
    await job(sessions, "match.lock_lineup", fixture_id)
    at = await job(sessions, "match.start", fixture_id)
    return fixture_id, at


class Source:
    def __init__(self, sessions):
        self.sessions = sessions

    @asynccontextmanager
    async def transaction(self):
        async with self.sessions.begin() as session:
            yield session


async def test_seed_retry_claim_race_and_unique_faces(sessions):
    _, _, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        retry = await initialize_world(session, OPTIONS, initialized_at=NOW)
        assert retry.created is False
        total = await session.scalar(select(func.count()).select_from(Person))
        distinct = await session.scalar(
            select(func.count(func.distinct(Person.appearance_identity_id)))
        )
        assert total == distinct == 1050
        a = await begin_game(session, telegram_id=1002, update_id=2, payload=None, at=NOW)
        b = await begin_game(session, telegram_id=1003, update_id=3, payload=None, at=NOW)
        target = a.offers[0].id
        assert target != club_id

    async def claim(actor):
        try:
            async with sessions.begin() as session:
                return (
                    await claim_club(
                        session,
                        replace(actor, operation_id=uuid4(), update_id=actor.update_id + 10),
                        target,
                    )
                ).manager_id
        except ClubUnavailable:
            return None

    values = await asyncio.wait_for(asyncio.gather(claim(a.actor), claim(b.actor)), timeout=10)
    assert sum(value is not None for value in values) == 1


async def test_lineup_stale_revision_snapshot_and_deadline(sessions):
    _, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        data = await load_lineup(session, actor)
    draft = LineupInput(
        tuple(StarterPick(p.person_id, p.slot_position) for p in data.starters),
        tuple(p.person_id for p in data.bench),
        data.formation,
        data.starters[0].person_id,
        data.tactic,
        "attack",
        data.version,
    )

    async def save(update):
        try:
            async with sessions.begin() as session:
                return await save_lineup(
                    session,
                    replace(actor, operation_id=uuid4(), update_id=update),
                    draft,
                    clock=lambda: NOW,
                )
        except LineupStale:
            return None

    result = await asyncio.wait_for(asyncio.gather(save(10), save(11)), timeout=10)
    assert sum(item is not None for item in result) == 1
    fixture_id, at = await start_fixture(sessions, club_id)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        side = (
            "home"
            if match.starting_snapshot["home"]["snapshot"]["club_id"] == str(club_id)
            else "away"
        )
        assert match.starting_snapshot[side]["mentality"] == "attack"
        data = await load_lineup(session, replace(actor, at=at))
        assert data.locked
        with pytest.raises(LineupLocked):
            await save_lineup(
                session,
                replace(actor, operation_id=uuid4(), update_id=12, at=at),
                replace(draft, expected_version=data.version),
                clock=lambda: at,
            )
        command = await session.scalar(
            select(AcceptedCommand).where(AcceptedCommand.kind == "lineup")
        )
        assert command.telegram_update_id in {10, 11}


async def test_outbox_parallel_stream_claim_and_edit(sessions):
    _, _, club_id = await setup_world(sessions)
    fixture_id, at = await start_fixture(sessions, club_id)
    await job(sessions, "match.tick", fixture_id, 1)
    now = at + timedelta(seconds=61)
    async with sessions.begin() as session:
        first = (await claim_outbox(session, now, lease_owner="sender-a"))[0]
        first_id, first_attempt = first.id, first.attempts
    async with sessions.begin() as session:
        assert not await claim_outbox(session, now, lease_owner="sender-b")
        await finish_delivery(
            session,
            first_id,
            now,
            Outcome.SENT,
            lease_owner="sender-a",
            attempt=first_attempt,
            telegram_message_id=901,
        )

    class Transport:
        calls = []

        async def send_text(self, *args):
            self.calls.append("send")
            return SendResult(902)

        async def edit_text(self, chat_id, message_id, text):
            assert message_id == 901
            self.calls.append("edit")

    transport = Transport()
    assert (await process_one_delivery(Source(sessions), transport, clock=lambda: now)).processed
    assert transport.calls == ["edit"]
    async with sessions.begin() as session:
        board = await session.scalar(
            select(OutboxBoard).where(OutboxBoard.fixture_id == fixture_id)
        )
        assert board.telegram_message_id == 901 and board.revision == 10


async def test_full_match_report_history_and_immutable_snapshot(sessions):
    _, actor, club_id = await setup_world(sessions)
    fixture_id, at = await start_fixture(sessions, club_id)
    for tick in range(1, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture_id)
        await job(sessions, "match.tick", fixture_id, tick)
    async with sessions.begin() as session:
        actor = replace(actor, operation_id=uuid4(), at=at + timedelta(minutes=34))
        report = await get_match_report(session, actor, fixture_id)
        stored_hash = report.state_hash
        fixture = await session.get(Fixture, fixture_id)
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        assert fixture.status == "finished" and match.completed_tick == 30
        assert await session.scalar(select(func.count()).select_from(MatchResult)) == 1
        own = await session.scalar(select(Standing).where(Standing.club_id == club_id))
        assert own.played == 1
    database = Database(Settings())
    try:
        async with database.read_connection() as connection:
            snapshot = await collect_snapshot(connection, now=actor.at)
        assert snapshot.matches[0].tick_numbers == tuple(range(1, 31))
        report = collect_report(snapshot, late_threshold_seconds=180, appearance_low_threshold=0)
        assert "TICK_GAP_OR_COUNT_MISMATCH" not in {f.code for f in report.findings}
    finally:
        await database.close()
    # Replaying the final tick cannot award the match twice.
    await job(sessions, "match.tick", fixture_id, 30)
    async with sessions.begin() as session:
        report2 = await get_match_report(session, replace(actor, operation_id=uuid4()), fixture_id)
        assert report2.state_hash == stored_hash
        own = await session.scalar(select(Standing).where(Standing.club_id == club_id))
        assert own.played == 1


async def test_portrait_cache_survives_new_session(sessions):
    values = dict(
        identity_id="dugout-whole-v2/p0001",
        variant_id="p0001-player-adult-l1",
        catalog_version=2,
        kit_primary="#743D42",
        kit_secondary="#F2E9D8",
        kit_version=1,
        size=256,
        background="transparent",
    )
    key = portrait_cache_key(**values)
    async with sessions.begin() as session:
        await store_file_id(session, cache_key=key, file_id="fake-telegram-file", at=NOW, **values)
    async with sessions.begin() as session:
        assert await load_file_id(session, key) == "fake-telegram-file"
