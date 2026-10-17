"""Proactive ready club stock and restartable isolated bot history on one PG DB."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from goaletto.application.onboarding import ClubUnavailable, begin_game, claim_club
from goaletto.application.world import initialize_world
from goaletto.application.world_capacity import RESERVE_JOB, available_clubs
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    Job,
    League,
    LeagueSet,
    Match,
    MatchResult,
    OperationAttempt,
    OwnershipPeriod,
    Person,
    Season,
    Standing,
    WorldAdmission,
    WorldSeedReceipt,
)
from tests.integration.test_agent_wave import KICKOFF, NOW, OPTIONS

pytestmark = pytest.mark.integration
AT = KICKOFF + timedelta(days=1)


async def seed(sessions, *, internal=False):
    async with sessions.begin() as session:
        world = await initialize_world(session, OPTIONS, initialized_at=NOW)
        # Original calendar deliberately remains held. Only the newly generated
        # owner-free world may fast-forward; the original set is untouched.
        entry = await begin_game(
            session, telegram_id=1001, update_id=1, payload=None, at=NOW, internal=internal
        )
        actor = replace(entry.actor, operation_id=uuid4(), update_id=2)
        owner = await claim_club(session, actor, entry.offers[0].id)
    return world, actor, owner


async def prepare_one(sessions, *, at=AT):
    async with sessions.begin() as session:
        assert await process_one_job(session, at=at)
        admission = await session.scalar(select(WorldAdmission))
        assert admission is not None and admission.ready_at is None
        return admission.season_id


async def drain(sessions, season_id, *, at=AT, limit=1500):
    for _ in range(limit):
        async with sessions.begin() as session:
            if (await session.get(WorldAdmission, season_id)).ready_at is not None:
                return
            assert await process_one_job(session, at=at)
    raise AssertionError("Bot replay did not finish")


async def test_stock_replays_real_bot_matches_before_admission_and_keeps_common_calendar(sessions):
    world, actor, owner = await seed(sessions)
    season_id = await prepare_one(sessions)
    async with sessions.begin() as session:
        admission = await session.get(WorldAdmission, season_id)
        source = await session.get(Season, world.season_id)
        new = await session.get(Season, season_id)
        assert (new.cycle_epoch, new.starts_at, new.ends_at) == (
            source.cycle_epoch,
            source.starts_at,
            source.ends_at,
        )
        assert admission.reference_season_id == source.id
        assert await available_clubs(session) == 9
        hidden = await session.scalar(
            select(Club.id)
            .join(League)
            .where(League.league_set_id == admission.league_set_id, League.tier == 5)
        )
        another = await begin_game(session, telegram_id=1002, update_id=3, payload=None, at=AT)
        assert hidden not in {o.id for o in another.offers}
        with pytest.raises(ClubUnavailable):
            await claim_club(session, replace(another.actor, operation_id=uuid4()), hidden)
    await drain(sessions, season_id)
    async with sessions.begin() as session:
        admission = await session.get(WorldAdmission, season_id)
        assert admission.ready_at == AT
        assert await available_clubs(session) == 19
        assert await session.scalar(select(func.count()).select_from(LeagueSet)) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MatchResult)
                .where(MatchResult.season_id == season_id, MatchResult.competition == "league")
            )
            == 25
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MatchResult)
                .where(MatchResult.season_id == season_id, MatchResult.competition == "cup")
            )
            > 0
        )
        assert (
            await session.scalar(
                select(func.sum(Standing.played)).where(Standing.season_id == season_id)
            )
            == 50
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Fixture).where(Fixture.season_id == source.id)
            )
            == 0
        )  # Never replay the existing world.
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Match)
                .where(
                    Match.fixture_id.in_(select(Fixture.id).where(Fixture.season_id == source.id))
                )
            )
            == 0
        )
        for result in (
            await session.scalars(select(MatchResult).where(MatchResult.season_id == season_id))
        ).all():
            assert result.actual_finish_at <= AT and result.recorded_at == AT
        # Reuse the existing bank: distinct people, stable assignments, no new art.
        assert await session.scalar(select(func.count()).select_from(Person)) > 2000
        assert (
            await session.scalar(select(func.count(func.distinct(Person.appearance_identity_id))))
            <= 1080
        )
        old = await session.get(OwnershipPeriod, owner.id)
        assert old.last_game_action_at == NOW
        post = await begin_game(session, telegram_id=1003, update_id=4, payload=None, at=AT)
        # The list is paginated globally: hidden club becomes claimable even if
        # UUID ordering places it beyond the first page.
        signed = await claim_club(session, replace(post.actor, operation_id=uuid4()), hidden)
        assert signed.club_id == hidden
        # Replaying an admitted set is forbidden, even with an explicit caller.
        with pytest.raises(ValueError, match="unopened admission"):
            await process_one_job(session, at=AT, replay_season_id=season_id)
        assert (await session.get(WorldSeedReceipt, "auto-0002")).created_at == AT


@pytest.mark.parametrize("internal", [False, True])
async def test_registration_only_queues_reserve_and_same_start_retry_never_renews(
    sessions, monkeypatch, internal
):
    _, _, owner = await seed(sessions, internal=internal)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Generation must never run in a user request")

    monkeypatch.setattr("goaletto.application.world_capacity.initialize_world", forbidden)
    async with sessions.begin() as session:
        before = (await session.get(OwnershipPeriod, owner.id)).expires_at
        # First /start had no club; retry after claim must not become club_opened.
        repeated = await begin_game(
            session,
            telegram_id=1001,
            update_id=1,
            payload=None,
            at=NOW + timedelta(days=2),
            internal=internal,
        )
        assert repeated.club_id == owner.club_id
        lease = await session.get(OwnershipPeriod, owner.id)
        assert lease.last_game_action_at == NOW and lease.expires_at == before
        assert await session.scalar(select(func.count()).select_from(LeagueSet)) == 1
        assert (await session.get(Job, RESERVE_JOB)).status == "pending"
        new = await begin_game(
            session, telegram_id=1001, update_id=9, payload=None, at=NOW + timedelta(days=2)
        )
        assert new.club_id == owner.club_id
        assert lease.last_game_action_at == NOW + timedelta(days=2)


async def test_concurrent_reserve_is_one_set_and_pending_clubs_reject_database_claims(sessions):
    await seed(sessions)

    async def reserve():
        async with sessions.begin() as session:
            return await process_one_job(session, at=AT)

    await asyncio.wait_for(asyncio.gather(reserve(), reserve()), 30)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(WorldAdmission)) == 1
        admission = await session.scalar(select(WorldAdmission))
        hidden = await session.scalar(
            select(Club)
            .join(League)
            .where(League.league_set_id == admission.league_set_id, League.tier == 5)
        )
        with pytest.raises(DBAPIError, match="still preparing"):
            async with session.begin_nested():
                hidden.owner_telegram_id = 123456
                await session.flush()
        assert await available_clubs(session) == 9


async def test_replay_crash_rolls_back_one_batch_then_resumes_without_duplicate_results(
    sessions, monkeypatch
):
    import goaletto.infrastructure.jobs as worker

    await seed(sessions)
    season_id = await prepare_one(sessions)
    async with sessions.begin() as session:
        assert await process_one_job(session, at=AT)
        ticks = await session.scalar(select(func.sum(Match.completed_tick))) or 0
        assert ticks >= 0
    original = worker.handle_calendar_job

    async def crash(*args, **kwargs):
        raise RuntimeError("Synthetic replay interruption")

    monkeypatch.setattr(worker, "handle_calendar_job", crash)
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await process_one_job(session, at=AT)
    async with sessions.begin() as session:
        assert (await session.scalar(select(func.sum(Match.completed_tick))) or 0) == ticks
        assert (await session.get(WorldAdmission, season_id)).ready_at is None
    monkeypatch.setattr(worker, "handle_calendar_job", original)
    await drain(sessions, season_id)
    async with sessions.begin() as session:
        assert await available_clubs(session) == 19
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MatchResult)
                .where(MatchResult.season_id == season_id, MatchResult.competition == "league")
            )
            == 25
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OperationAttempt)
                .where(OperationAttempt.stage.like("world.catch_up:%"))
            )
            > 100
        )


async def test_new_unowned_world_finishes_all_past_league_and_cup_before_release(sessions):
    from goaletto.domain.game_cycle import season_end
    from goaletto.infrastructure.models import CupTie, MatchTick

    await seed(sessions)
    at = season_end(KICKOFF) + timedelta(hours=1)
    sid = await prepare_one(sessions, at=at)
    await drain(sessions, sid, at=at)
    async with sessions.begin() as session:
        assert (await session.get(WorldAdmission, sid)).ready_at == at
        assert await available_clubs(session) == 19
        league = await session.scalar(
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.season_id == sid, MatchResult.competition == "league")
        )
        cup = await session.scalar(
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.season_id == sid, MatchResult.competition == "cup")
        )
        assert (league, cup) == (450, 97)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CupTie)
                .where(CupTie.season_id == sid, CupTie.status == "decided")
            )
            == 49
        )
        assert (
            await session.scalar(select(func.sum(Standing.played)).where(Standing.season_id == sid))
            == 900
        )
        # Historical games use actual checkpoints, including any real extra time
        # and penalties, instead of writing fabricated final scores/tables.
        assert (
            await session.scalar(
                select(func.count())
                .select_from(MatchTick)
                .join(Match, Match.id == MatchTick.match_id)
                .join(Fixture, Fixture.id == Match.fixture_id)
                .where(Fixture.season_id == sid)
            )
            >= 547 * 30
        )
        assert (await session.get(Season, sid)).status == "active"
        # The untouched compatible source is unfinished: neither set can roll alone.
        assert (
            await session.scalar(select(func.count()).select_from(Season).where(Season.number == 2))
            == 0
        )


async def test_database_refuses_historical_admission_for_previously_owned_club(sessions):
    world, _, _ = await seed(sessions)
    async with sessions.begin() as session:
        with pytest.raises(DBAPIError, match="new unowned set"):
            async with session.begin_nested():
                session.add(
                    WorldAdmission(
                        season_id=world.season_id,
                        league_set_id=world.league_set_id,
                        reference_season_id=None,
                        replay_from=NOW,
                        requested_at=AT,
                        ready_at=None,
                    )
                )
                await session.flush()
        assert await session.scalar(select(func.count()).select_from(WorldAdmission)) == 0


@pytest.mark.parametrize("entry_first", [False, True])
async def test_bot_history_before_analytics_rollout_does_not_fake_observation_coverage(
    sessions, entry_first
):
    from goaletto.application.world_capacity import request_reserve
    from goaletto.infrastructure.models import AnalyticsCoverage, JourneyEventRow, MatchTick

    entry_at = KICKOFF - timedelta(hours=1)
    async with sessions.begin() as session:
        await initialize_world(session, OPTIONS, initialized_at=NOW)
        if entry_first:
            entry = await begin_game(
                session, telegram_id=1001, update_id=1, payload=None, at=entry_at
            )
            await claim_club(
                session, replace(entry.actor, operation_id=uuid4()), entry.offers[0].id
            )
        else:
            await request_reserve(session, AT)
    sid = await prepare_one(sessions)
    for _ in range(5):
        async with sessions.begin() as session:
            assert await process_one_job(session, at=AT)
    async with sessions.begin() as session:
        coverage_at = entry_at if entry_first else AT
        assert (await session.scalar(select(AnalyticsCoverage))).started_at == coverage_at
        assert (await session.get(WorldAdmission, sid)).ready_at is None
        assert await session.scalar(select(func.count()).select_from(MatchTick)) > 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JourneyEventRow)
                .where(JourneyEventRow.occurred_at < coverage_at)
            )
            == 0
        )


async def test_new_set_copies_global_season_number_without_inventing_previous_years(sessions):
    world, _, _ = await seed(sessions)
    async with sessions.begin() as session:
        (await session.get(Season, world.season_id)).number = 7
    sid = await prepare_one(sessions)
    async with sessions.begin() as session:
        fresh = await session.get(Season, sid)
        assert fresh.number == 7
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Season)
                .where(Season.league_set_id == fresh.league_set_id)
            )
            == 1
        )
        assert (await session.get(WorldSeedReceipt, "auto-0002")).config["season_number"] == 7
