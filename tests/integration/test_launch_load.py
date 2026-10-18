"""Bounded launch load: real PG/clock/ticks/Dispatcher, fake Telegram only.

Resets the one player-free application DB via the normal integration fixture.
Synthetic setup holds unrelated historical jobs; no past results are invented.
Only the first simultaneous live tick is measured, never called a season/HDC test.
"""

import asyncio
import json
import os
import platform
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select, text, update

from goaletto.application.live_commands import load_live_screen
from goaletto.application.market import list_player, place_bid
from goaletto.application.match_runtime import activate_season, handle_calendar_job
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.world import initialize_world
from goaletto.bot.live_views import callback
from goaletto.bot.main import build_dispatcher
from goaletto.config import Settings
from goaletto.domain.match_types import Mentality
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.models import (
    AcceptedCommand,
    Auction,
    AuctionBid,
    Club,
    FinanceAccount,
    Fixture,
    Job,
    League,
    LineupSlot,
    Match,
    MatchTick,
    MoneyReservation,
    OperationAttempt,
    OwnershipPeriod,
    Player,
    SeasonClub,
)
from goaletto.infrastructure.runtime_observation import report
from tests.integration.test_game_dispatcher import Telegram

pytestmark = pytest.mark.integration


class ConcurrentSource:
    def __init__(self, sessions):
        self.sessions = sessions
        self._depth = ContextVar("load_transaction_depth", default=0)

    @property
    def depth(self):
        return self._depth.get()

    @asynccontextmanager
    async def transaction(self):
        assert self.depth == 0
        token = self._depth.set(1)
        try:
            async with self.sessions.begin() as session:
                yield session
        finally:
            self._depth.reset(token)


def distribution(values):
    return {
        "count": len(values),
        "p50_seconds": median(values),
        "p95_seconds": quantiles(values, n=100, method="inclusive")[94]
        if len(values) > 1
        else values[0],
    }


async def test_multiset_live_tick_registration_bid_and_callback_load(sessions):
    async with asyncio.timeout(240):
        await _run_profile(sessions)


async def _run_profile(sessions):
    sets = int(os.environ.get("GOALETTO_LOAD_SETS", "2"))
    workers = int(os.environ.get("GOALETTO_LOAD_WORKERS", "4"))
    poll_seconds = Settings().worker_poll_seconds
    assert 1 <= sets <= 3 and 1 <= workers <= 8
    now = datetime.now(UTC)
    # Midseason window is open now. The unplayed synthetic historical calendar
    # is held, not replayed or counted in performance observations.
    kickoff = (now - timedelta(days=32)).replace(hour=18, minute=0, second=0, microsecond=0)
    setup_at = kickoff - timedelta(days=4)
    for number in range(sets):
        async with sessions.begin() as session:
            world = await initialize_world(
                session,
                WorldOptions(
                    key=f"launch-load-{number}",
                    seed=number,
                    first_kickoff=kickoff,
                    initial_budget=600000,
                ),
                initialized_at=setup_at,
            )
            await activate_season(session, world.season_id, setup_at)
            opening = await session.scalar(
                select(Job).where(
                    Job.kind == "season.open",
                    Job.payload["season_id"].astext == str(world.season_id),
                )
            )
            await handle_calendar_job(session, opening, opening.due_at)
            opening.status = "done"

    async def register(uid):
        started = perf_counter()
        async with sessions.begin() as session:
            entry = await begin_game(
                session,
                telegram_id=uid,
                update_id=uid,
                payload=None,
                at=datetime.now(UTC),
                internal=True,
            )
            actor = replace(entry.actor, operation_id=uuid4())
            owner = await claim_club(session, actor, entry.offers[0].id)
            league_set = await session.scalar(
                select(League.league_set_id)
                .join(Club, Club.league_id == League.id)
                .where(Club.id == owner.club_id)
            )
            result = (actor, owner.id, owner.club_id, league_set)
        return result, perf_counter() - started

    pre = [await register(20000 + i) for i in range(5)]
    owners = [value[0] for value in pre]
    same_set = max(({o[3]: [p for p in owners if p[3] == o[3]] for o in owners}).values(), key=len)
    seller, bidders = same_set[0], same_set[1:]
    async with sessions.begin() as session:
        person = await session.scalar(
            select(Player.person_id)
            .join(LineupSlot, LineupSlot.person_id == Player.person_id)
            .where(Player.club_id == seller[2], LineupSlot.slot == 2)
        )
        auction = await list_player(
            session,
            replace(seller[0], operation_id=uuid4(), at=datetime.now(UTC)),
            seller[1],
            person,
            100000,
        )
        auction_id = auction.id
        fixtures = (
            await session.scalars(
                select(Fixture)
                .where(Fixture.competition == "league", Fixture.round_number == 1)
                .order_by(Fixture.id)
            )
        ).all()
        fixture_ids = [f.id for f in fixtures]
    assert len(fixture_ids) == 25 * sets
    # Lock before starting any real 60-second decision window.
    for fixture in fixture_ids:
        async with sessions.begin() as session:
            lock = await session.scalar(
                select(Job).where(
                    Job.kind == "match.lock_lineup",
                    Job.payload["fixture_id"].astext == str(fixture),
                )
            )
            await handle_calendar_job(session, lock, lock.due_at)
            lock.status = "done"
    for fixture in fixture_ids:
        async with sessions.begin() as session:
            start = await session.scalar(
                select(Job).where(
                    Job.kind == "match.start", Job.payload["fixture_id"].astext == str(fixture)
                )
            )
            await handle_calendar_job(session, start, datetime.now(UTC))
            start.status = "done"
    since = datetime.now(UTC)
    source = ConcurrentSource(sessions)
    telegram = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=telegram)
    dispatcher = build_dispatcher(source, game_enabled=True)

    async def bid(index):
        started = perf_counter()
        buyer = bidders[index % len(bidders)]
        try:
            async with sessions.begin() as session:
                result = await place_bid(
                    session,
                    replace(buyer[0], operation_id=uuid4(), at=datetime.now(UTC)),
                    buyer[1],
                    auction_id,
                    100000 + index * 5000,
                )
                assert result.accepted
        except ValueError as error:
            assert str(error) == "Bid below current minimum"
        return perf_counter() - started

    async def click(index, owner, data="lv:open"):
        user = User(id=owner[0].telegram_id, is_bot=False, first_name="Synthetic")
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=30000 + index,
                callback_query=CallbackQuery(
                    id=f"synthetic-load-{index}",
                    from_user=user,
                    chat_instance="local",
                    message=Message(
                        message_id=1, date=datetime.now(UTC), chat=Chat(id=user.id, type="private")
                    ),
                    data=data,
                ),
            ),
        )

    try:
        requests = min(5, 10 * sets - len(owners))
        new, bid_times, _ = await asyncio.gather(
            asyncio.gather(*(register(21000 + i) for i in range(requests))),
            asyncio.gather(*(bid(i) for i in range(20))),
            asyncio.gather(*(click(i, owners[i % len(owners)]) for i in range(20))),
        )
        owners.extend(value[0] for value in new)
        commands = []
        for index, owner in enumerate(owners[:5]):
            async with sessions.begin() as session:
                screen = await load_live_screen(
                    session, replace(owner[0], at=datetime.now(UTC)), record=False
                )
            commands.append(
                click(
                    100 + index,
                    owner,
                    callback(screen, "m", list(Mentality).index(Mentality.ATTACK)),
                )
            )
        await asyncio.gather(*commands)
        async with sessions.begin() as session:
            await session.execute(
                update(Job)
                .where(
                    Job.status == "pending",
                    (Job.kind != "match.tick")
                    | ~Job.payload["fixture_id"].astext.in_([str(i) for i in fixture_ids]),
                )
                .values(status="held")
            )
            due = await session.scalar(
                select(func.min(Job.due_at)).where(
                    Job.kind == "match.tick", Job.status == "pending"
                )
            )
        delay = (due - datetime.now(UTC)).total_seconds()
        # Real first-tick deadlines. No future clock injection, no tick burst.
        await asyncio.sleep(max(0, delay))
        tick_started = perf_counter()

        async def drain():
            async with asyncio.timeout(90):
                for _ in range(500):
                    async with sessions.begin() as session:
                        remaining = await session.scalar(
                            select(func.count()).select_from(Match).where(Match.completed_tick == 0)
                        )
                    if not remaining:
                        return
                    async with sessions.begin() as session:
                        processed = await process_one_job(session)
                    if not processed:
                        await asyncio.sleep(poll_seconds)
                raise AssertionError("Bounded live batch did not drain")

        await asyncio.gather(
            *(drain() for _ in range(workers)),
            *(click(200 + i, owners[i % len(owners)]) for i in range(20)),
        )
        elapsed = perf_counter() - tick_started
    finally:
        await bot.session.close()
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(MatchTick)) == 25 * sets
        assert (
            await session.scalar(
                select(func.count()).select_from(Match).where(Match.completed_tick == 1)
            )
            == 25 * sets
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AcceptedCommand)
                .where(AcceptedCommand.status == "applied")
            )
            == 5
        )
        memberships = (
            await session.execute(
                select(SeasonClub.league_id, func.count()).group_by(SeasonClub.league_id)
            )
        ).all()
        assert len(memberships) == 5 * sets and all(count == 10 for _, count in memberships)
        assert await session.scalar(
            select(func.count())
            .select_from(OwnershipPeriod)
            .where(OwnershipPeriod.ended_at.is_(None))
        ) == len(owners)
        highest = await session.get(
            AuctionBid, (await session.get(Auction, auction_id)).highest_bid_id
        )
        assert highest.amount == 195000
        assert (await session.get(Player, person)).club_id == seller[2]
        assert (
            await session.scalar(
                select(func.sum(MoneyReservation.amount)).where(MoneyReservation.status == "active")
            )
            == 195000
        )
        assert await session.scalar(select(func.sum(FinanceAccount.reserved))) == 195000
        failed = await session.scalar(
            select(func.count())
            .select_from(OperationAttempt)
            .where(OperationAttempt.outcome == "failed")
        )
        assert failed == 0
        observations = await report(session, since, datetime.now(UTC))
        metrics = {m["metric"]: m for m in observations["metrics"]}
        assert metrics["worker_lateness"]["measured"] >= 25 * sets
        assert metrics["request_total"]["measured"] == 45
        assert observations["request_outcomes"] == [
            {"business_outcome": "committed", "delivery_outcome": "accepted", "attempts": 45}
        ]
        committed_ticks = dict(
            (
                await session.execute(
                    text("""
          SELECT count(*) AS count,
            percentile_cont(0.95) WITHIN GROUP
              (ORDER BY extract(epoch FROM finished_at-due_at)) AS completion_lateness_p95_seconds,
            percentile_cont(0.95) WITHIN GROUP
              (ORDER BY extract(epoch FROM finished_at-started_at)) AS processing_p95_seconds
          FROM operation_attempts WHERE stage='match.tick' AND outcome='committed'
            AND started_at >= :since
        """),
                    {"since": since},
                )
            )
            .mappings()
            .one()
        )
        assert committed_ticks["count"] == 25 * sets
        deferred = await session.scalar(
            select(func.count())
            .select_from(OperationAttempt)
            .where(OperationAttempt.stage == "match.tick", OperationAttempt.outcome == "deferred")
        )
        postgres = await session.scalar(select(func.version()))
    result = {
        "scope": "first live tick on synthetic held-history worlds; real UTC; fake Telegram",
        "sets": sets,
        "simultaneous_matches": len(fixture_ids),
        "workers": workers,
        "worker_poll_seconds": poll_seconds,
        "committed_ticks": committed_ticks,
        "deferred_tick_attempts": deferred,
        "machine": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "postgres": postgres,
        "tick_batch_seconds": elapsed,
        "registrations": distribution([value[1] for value in new]),
        "bids": distribution(bid_times),
        "metrics": observations["metrics"],
        "request_outcomes": observations["request_outcomes"],
        "invariants": "passed",
        "hdc_capacity_proven": False,
    }
    target = Path(os.environ.get("GOALETTO_LOAD_REPORT", "work/launch-load-report.json"))
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text, json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
