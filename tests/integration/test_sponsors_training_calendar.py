"""Frozen sponsor intervals and future choices on the single development DB."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.calendar_planning import recover_unannounced_plan
from goaletto.application.finances import cycle_finances, load_finances
from goaletto.application.outbox_delivery import Decision, check_recipient
from goaletto.application.recovery import recover_player
from goaletto.application.sponsorship import sign_sponsor, sponsor_for_period
from goaletto.application.training import (
    apply_training,
    load_training,
    reserve_match_day,
    select_training,
    week_start,
)
from goaletto.bot.finance_views import finance_view
from goaletto.domain.sponsorship import sponsor_terms
from goaletto.domain.training import Focus, Intensity
from goaletto.infrastructure.models import (
    Club,
    FinanceCycle,
    Fixture,
    FixturePlan,
    Job,
    OutboxMessage,
    OwnershipPeriod,
    Player,
    Season,
    SponsorContract,
    Standing,
    TrainingPlan,
)
from goaletto.presentation.i18n import using_locale
from tests.integration.test_agent_wave import NOW
from tests.integration.test_candidates import action, funded
from tests.integration.test_specialist_training import training_job

pytestmark = pytest.mark.integration


async def choose(session, actor, owner, day):
    return await select_training(
        session,
        action(actor),
        ownership_id=owner,
        week=day,
        focus=Focus.TEAM,
        intensity=Intensity.MEDIUM,
        clock=lambda: actor.at,
    )


async def test_opening_sponsor_is_fixed_and_partial_invoice_replay_does_not_reprice(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(SponsorContract)) == 50
        contract = await session.scalar(
            select(SponsorContract).where(SponsorContract.club_id == club_id)
        )
        assert contract.basis == "opening" and contract.place is None
        assert contract.cycle_rate == sponsor_terms(5, None, 2500).cycle_rate
        club = await session.get(Club, club_id)
        club.supporters = 99999
        standing = await session.get(Standing, (contract.season_id, club_id))
        standing.played = standing.won = 18
        standing.points = 54
        screen = await load_finances(session, action(actor))
        assert screen.sponsorship.id == contract.id
        assert screen.sponsor == contract.cycle_rate
        for locale, label in (("ru", "Сезонный спонсор"), ("en", "Seasonal sponsor")):
            with using_locale(locale):
                body, _ = finance_view(screen)
                assert label in body and "2500" in body
        work = await session.scalar(
            select(Job).where(
                Job.kind == "finance.cycle", Job.payload["club_id"].astext == str(club_id)
            )
        )
        await cycle_finances(session, work, work.due_at + timedelta(days=7))
        invoice = await session.scalar(select(FinanceCycle).where(FinanceCycle.club_id == club_id))
        snapshot = invoice.snapshot["sponsor_periods"]
        assert len(snapshot) == 1 and snapshot[0]["rate"] == contract.cycle_rate
        assert snapshot[0]["supporters"] == 2500
        balance = club.budget
        await cycle_finances(session, work, work.due_at + timedelta(days=10))
        assert club.budget == balance and invoice.snapshot["sponsor_periods"] == snapshot


async def test_contract_boundary_offseason_rounding_retry_and_database_immutability(sessions):
    result, _, club_id = await funded(sessions)
    split = NOW + timedelta(days=2)
    async with sessions.begin() as session:
        club = await session.get(Club, club_id)
        old = await session.scalar(
            select(SponsorContract).where(SponsorContract.club_id == club_id)
        )
        season = await session.get(Season, result.season_id)
        next_season = Season(
            id=uuid4(),
            league_set_id=season.league_set_id,
            number=2,
            seed=1,
            status="scheduled",
            starts_at=split + timedelta(days=8),
            ends_at=split + timedelta(days=80),
        )
        session.add(next_season)
        await session.flush()
        contract = await sign_sponsor(
            session,
            club,
            next_season.id,
            split,
            next_season.ends_at,
            tier=4,
            place=1,
            source_season_id=season.id,
        )
        assert old.ends_at == contract.starts_at == split
        assert contract.cycle_rate == 59500
        club.supporters = 1
        replay = await sign_sponsor(
            session,
            club,
            next_season.id,
            split + timedelta(days=3),
            next_season.ends_at,
            tier=5,
            place=10,
            source_season_id=season.id,
        )
        assert replay.id == contract.id and replay.supporters == 2500
        end = NOW + timedelta(days=4)
        total, pieces = await sponsor_for_period(session, club_id, NOW, end)
        assert total == 25500 + 29750 and len(pieces) == 2
        parts = [NOW, NOW + timedelta(microseconds=1), split, end]
        assert (
            sum(
                [
                    (await sponsor_for_period(session, club_id, a, b))[0]
                    for a, b in zip(parts, parts[1:], strict=False)
                ]
            )
            == total
        )
        # The new contract starts at rollover, covering all eight offseason days.
        assert (await sponsor_for_period(session, club_id, split, split + timedelta(days=8)))[
            0
        ] == 119000
        contract_id = contract.id
    for sql in (
        "UPDATE sponsor_contracts SET cycle_rate=1 WHERE id=:id",
        "UPDATE sponsor_contracts SET supporters=1 WHERE id=:id",
        "DELETE FROM sponsor_contracts WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with sessions.begin() as session:
                await session.execute(text(sql), {"id": contract_id})
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            new_season = Season(
                id=uuid4(),
                league_set_id=result.league_set_id,
                number=3,
                seed=2,
                status="scheduled",
                starts_at=end,
                ends_at=end + timedelta(days=72),
            )
            session.add(new_season)
            await session.flush()
            await sign_sponsor(
                session,
                await session.get(Club, club_id),
                new_season.id,
                end,
                new_season.ends_at,
                tier=4,
                place=5,
                source_season_id=result.season_id,
            )
            raise RuntimeError("transaction rollback")
    async with sessions.begin() as session:
        assert (await session.get(SponsorContract, contract_id)).ends_at is None


async def test_future_dates_include_conditional_matches_and_allow_multiple_sessions(sessions):
    result, actor, club = await funded(sessions)
    day = week_start(NOW)
    async with sessions.begin() as session:
        owner = (await load_training(session, actor, record=False)).ownership_id
        conditional = FixturePlan(
            id=uuid4(),
            season_id=result.season_id,
            competition="cup",
            round_number=1,
            assigned_day=(day + timedelta(days=1)).date(),
            conditional_ref="winner:future-tie",
            possible_club_ids=[str(club)],
        )
        session.add(conditional)
        await session.flush()
        screen = await load_training(
            session, action(actor), day=day + timedelta(days=1), record=False
        )
        assert len(screen.dates) == 10
        assert screen.blocked and screen.block_reason.reason == "conditional"
        assert screen.block_reason.competition == "cup"
        with pytest.raises(ValueError, match="match day"):
            await choose(session, actor, owner, day + timedelta(days=1))
        # An eliminated club is no longer in the conditional pool, without materialized jobs.
        conditional.possible_club_ids = []
        await session.flush()
        assert not (
            await load_training(session, action(actor), day=day + timedelta(days=1), record=False)
        ).blocked
        plans = [await choose(session, actor, owner, day + timedelta(days=i)) for i in (1, 2, 3)]
        assert len({p.id for p in plans}) == 3
        player = await session.scalar(
            select(Player.person_id).where(Player.club_id == club).limit(1)
        )
        recovery = await recover_player(session, player, plans[0].due_at - timedelta(seconds=60))
        recovery.energy_bps = 5000
        for plan in plans:
            await apply_training(session, await training_job(session, plan), plan.due_at)
            assert plan.status == "applied" and len(plan.results) == 20
            if plan is plans[0]:
                assert 0 < recovery.energy_bps < 5500  # A new UTC date is not a full rest.
        assert await session.scalar(select(func.count()).select_from(TrainingPlan)) == 3
        for invalid in (day - timedelta(days=1), day + timedelta(days=10)):
            with pytest.raises(ValueError, match="horizon"):
                await choose(session, actor, owner, invalid)
        late = replace(action(actor), at=day + timedelta(days=2))
        with pytest.raises(ValueError, match="horizon"):
            await choose(session, late, owner, day + timedelta(days=1))


async def test_official_match_cancels_once_notifies_and_friendly_preserves_training(sessions):
    _, actor, club = await funded(sessions)
    day = week_start(NOW) + timedelta(days=1)
    async with sessions.begin() as session:
        owner = (await load_training(session, actor, record=False)).ownership_id
        plan = await choose(session, actor, owner, day)
        with pytest.raises(ValueError, match="occupies"):
            await reserve_match_day(session, (club,), day.date(), NOW, competition="friendly")
        await reserve_match_day(session, (club,), day.date(), NOW, competition="cup")
        await reserve_match_day(session, (club,), day.date(), NOW, competition="cup")
        assert plan.status == "cancelled" and plan.results == [{"reason": "match_day"}]
        notices = (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.payload["kind"].astext == "training_cancelled"
                )
            )
        ).all()
        assert len(notices) == 1
        assert await check_recipient(session, notices[0], NOW) == Decision.DELIVER
        ownership = await session.get(OwnershipPeriod, owner)
        assert (
            await check_recipient(session, notices[0], ownership.expires_at) == Decision.SKIP_STALE
        )
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "cancelled" and plan.applied_at is None
        second = await choose(session, actor, owner, day + timedelta(days=1))
        await apply_training(session, await training_job(session, second), second.due_at)
        with pytest.raises(ValueError, match="occupies"):
            await reserve_match_day(
                session, (club,), second.week_start.date(), NOW, competition="friendly"
            )
        await reserve_match_day(
            session, (club,), second.week_start.date(), NOW, competition="league"
        )
        assert second.status == "applied"  # Mandatory fixtures cannot erase a finished receipt.


async def test_friendly_booking_and_training_selection_serialize(sessions):
    result, actor, club = await funded(sessions)
    day = week_start(NOW) + timedelta(days=1)
    async with sessions.begin() as session:
        owner = (await load_training(session, actor, record=False)).ownership_id
        opponent = await session.scalar(select(Club.id).where(Club.id != club).limit(1))

    async def training():
        try:
            async with sessions.begin() as session:
                await choose(session, actor, owner, day)
            return True
        except ValueError:
            return False

    async def friendly():
        try:
            async with sessions.begin() as session:
                await reserve_match_day(
                    session, (club, opponent), day.date(), NOW, competition="friendly"
                )
                session.add(
                    FixturePlan(
                        id=uuid4(),
                        season_id=result.season_id,
                        competition="friendly",
                        round_number=1,
                        assigned_day=day.date(),
                        home_club_id=club,
                        away_club_id=opponent,
                    )
                )
            return True
        except ValueError:
            return False

    assert sum(await asyncio.wait_for(asyncio.gather(training(), friendly()), 15)) == 1
    async with sessions.begin() as session:
        count = await session.scalar(select(func.count()).select_from(TrainingPlan))
        screen = await load_training(session, action(actor), day=day, record=False)
        assert (count == 1 and screen.plan.status == "scheduled") or (
            count == 0 and screen.blocked and screen.block_reason.competition == "friendly"
        )


async def test_unfinished_match_blocks_current_day_but_not_all_future_days(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        fixture = await session.scalar(
            select(Fixture)
            .where((Fixture.home_club_id == club) | (Fixture.away_club_id == club))
            .order_by(Fixture.scheduled_start_at)
        )
        fixture.status, fixture.actual_start_at = "paused", fixture.scheduled_start_at
        plan = await session.get(FixturePlan, fixture.id)
        plan.competition = "cup"
        at = week_start(fixture.scheduled_start_at) + timedelta(days=1, hours=1)
        current = replace(action(actor), at=at)
        screen = await load_training(session, current, record=False)
        assert screen.blocked and screen.block_reason.competition == "cup"
        assert screen.block_reason.opponent
        tomorrow = week_start(at) + timedelta(days=1)
        assert not (await load_training(session, current, day=tomorrow, record=False)).blocked
        with pytest.raises(ValueError, match="match day"):
            await choose(session, current, screen.ownership_id, week_start(at))
        future = await choose(session, current, screen.ownership_id, tomorrow)
        assert future.status == "scheduled"


async def test_moving_an_unannounced_official_plan_cancels_training_before_jobs_exist(sessions):
    result, actor, club = await funded(sessions)
    day = week_start(NOW) + timedelta(days=2)  # Cup phase, not an existing league date.
    async with sessions.begin() as session:
        owner = (await load_training(session, actor, record=False)).ownership_id
        training = await choose(session, actor, owner, day)
        plan = FixturePlan(
            id=uuid4(),
            season_id=result.season_id,
            competition="cup",
            round_number=1,
            conditional_ref="winner:moving-tie",
            possible_club_ids=[str(club)],
            assigned_day=(day + timedelta(days=4)).date(),
        )
        session.add(plan)
        await session.flush()
        await recover_unannounced_plan(session, plan.id, day, NOW)
        assert training.status == "cancelled" and plan.announced_at is None
        screen = await load_training(session, action(actor), day=day, record=False)
        assert screen.blocked and screen.block_reason.reason == "conditional"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.payload["kind"].astext == "training_cancelled")
            )
            == 1
        )
