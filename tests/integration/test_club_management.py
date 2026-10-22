"""Real services on the single disposable LOCAL application DB, never HDC."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.candidates import sign_candidate, supply_count
from goaletto.application.friendly import (
    challenge_opponents,
    invite_friendly,
    respond_friendly,
    schedule_friendly,
)
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.match_runtime import NotReady, activate_season, handle_calendar_job
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.player_release import confirm_release, quote_release
from goaletto.application.schedule import load_schedule
from goaletto.application.training import (
    apply_training,
    cancel_training,
    load_training,
    select_training,
    week_start,
)
from goaletto.application.world import initialize_world
from goaletto.domain.training import Focus, Intensity
from goaletto.infrastructure.models import (
    CandidateOffer,
    CandidateSigning,
    Club,
    Fixture,
    Job,
    League,
    LedgerEntry,
    LineupSlot,
    Manager,
    MatchResult,
    OwnershipPeriod,
    Person,
    Player,
    PlayerRelease,
    SalaryContract,
    SeasonClub,
    SeasonContestant,
    TrainingPlan,
)
from tests.integration.test_agent_wave import NOW, OPTIONS
from tests.integration.test_candidates import action, funded
from tests.integration.test_friendly import _free_day_and_opponent, _play

pytestmark = pytest.mark.integration


async def test_training_actual_preferred_time_calendar_cancel_and_retry(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as s:
        manager = await s.get(Manager, actor.manager_id)
        manager.timezone_name, manager.preferred_kickoff_minute = "Etc/GMT-14", 60
        # 01:00 local next day is 11:00 of the assigned UTC day.
        owner = await s.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        day = week_start(NOW) + timedelta(days=1)
        request = action(actor)
        plan = await select_training(
            s,
            request,
            ownership_id=owner.id,
            week=day,
            focus=Focus.TEAM,
            intensity=Intensity.MEDIUM,
            clock=lambda: NOW,
        )
        assert (
            plan.due_at == day + timedelta(hours=11)
            and plan.schedule_policy == "training-preference-v1"
        )
        pid, oid = plan.id, owner.id
        screen = await load_schedule(s, action(actor))
        event = next(e for d in screen.days for e in d.entries if e.competition == "training")
        assert event.kickoff == plan.due_at and event.focus == "team"
        job = await s.scalar(select(Job).where(Job.payload["plan_id"].astext == str(pid)))
        assert job.due_at == plan.due_at
        manager.preferred_kickoff_minute = 180
        assert (
            await load_training(s, action(actor), record=False, day=day)
        ).due_at == day + timedelta(hours=11)
        changed_at = NOW + timedelta(seconds=1)
        rescheduled = await select_training(
            s,
            action(actor),
            ownership_id=oid,
            week=day,
            focus=Focus.TEAM,
            intensity=Intensity.MEDIUM,
            clock=lambda: changed_at,
        )
        assert rescheduled.id == pid and rescheduled.due_at == day + timedelta(hours=13)
        await s.refresh(job)
        assert job.due_at == rescheduled.due_at
        manager.preferred_kickoff_minute = 240
        late = replace(action(actor), at=rescheduled.due_at + timedelta(seconds=1))
        with pytest.raises(ValueError, match="already started"):
            await select_training(
                s,
                late,
                ownership_id=oid,
                week=day,
                focus=Focus.TEAM,
                intensity=Intensity.MEDIUM,
                clock=lambda: late.at,
            )
        manager.preferred_kickoff_minute = 180
        with pytest.raises(NotReady):
            await apply_training(s, job, plan.due_at - timedelta(seconds=1))
        await cancel_training(s, action(actor), pid, oid)
        assert not [
            e
            for d in (await load_schedule(s, action(actor))).days
            for e in d.entries
            if e.competition == "training"
        ]
        newer = await select_training(
            s,
            action(actor),
            ownership_id=oid,
            week=day,
            focus=Focus.TEAM,
            intensity=Intensity.LOW,
            clock=lambda: NOW,
        )
        assert newer.id != pid
        await apply_training(s, job, job.due_at)
        assert (await s.get(TrainingPlan, pid)).status == "cancelled"
        newjob = await s.scalar(select(Job).where(Job.payload["plan_id"].astext == str(newer.id)))
        await apply_training(s, newjob, newjob.due_at)
        original = list(newer.results)
        await apply_training(s, newjob, newjob.due_at + timedelta(minutes=1))
        assert newer.status == "applied" and newer.results == original


async def test_cross_set_search_bot_and_human_invitation_full_runtime(sessions):
    first, actor, club_id = await funded(sessions)
    async with sessions.begin() as s:
        second = await initialize_world(
            s, replace(OPTIONS, key="global-friendly-second", seed=5), initialized_at=NOW
        )
        await activate_season(s, second.season_id, NOW)
        other = await s.scalar(
            select(Club)
            .join(SeasonClub, SeasonClub.club_id == Club.id)
            .where(SeasonClub.season_id == second.season_id)
            .order_by(Club.id)
            .limit(1)
        )
        other_id = other.id
        other.name = "Unique Opponent"
    _, _, day = await _free_day_and_opponent(sessions, club_id)
    async with sessions.begin() as s:
        found = await challenge_opponents(s, action(actor), day, "uNIQue opp")
        assert [x.club_id for x in found] == [other_id]
        assert not await challenge_opponents(s, action(actor), day, "%_")
        plan = await schedule_friendly(s, action(actor), other_id, day)
        pid = plan.id
        assert await s.get(SeasonContestant, (first.season_id, other_id))
        assert (await s.get(Fixture, pid)).competition == "friendly"
    async with sessions.begin() as s:
        for opening in (
            await s.scalars(select(Job).where(Job.kind == "season.open", Job.status != "done"))
        ).all():
            await handle_calendar_job(s, opening, opening.due_at)
            opening.status = "done"
    await _play(sessions, pid)
    async with sessions.begin() as s:
        assert await s.scalar(select(MatchResult.fixture_id).where(MatchResult.fixture_id == pid))
        # A human guest can see host-season plans in their own schedule.
        guest_club = await s.scalar(
            select(Club.id)
            .join(SeasonClub, SeasonClub.club_id == Club.id)
            .where(SeasonClub.season_id == second.season_id, Club.id != other_id)
            .join(League, League.id == Club.league_id)
            .where(League.tier == 5)
            .limit(1)
        )
        entry = await begin_game(s, telegram_id=1002, update_id=1002, payload=None, at=NOW)
        guest_actor = action(entry.actor)
        await claim_club(s, guest_actor, guest_club)
        invite = await invite_friendly(s, action(actor), guest_club, day + timedelta(days=4))
        answered = await respond_friendly(s, action(guest_actor), invite.id, accept=True)
        assert answered.status == "accepted"
        assert (
            await respond_friendly(s, action(guest_actor), invite.id, accept=True)
        ).plan_id == answered.plan_id
        guest_screen = await load_schedule(s, action(guest_actor), page=2)
        assert any(e.id == answered.plan_id for d in guest_screen.days for e in d.entries)
        human_pid = answered.plan_id
    await _play(sessions, human_pid)
    async with sessions.begin() as s:
        assert await s.scalar(
            select(MatchResult.fixture_id).where(MatchResult.fixture_id == human_pid)
        )


async def test_release_compensation_once_repeated_signings_keep_identity_and_wages(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as s:
        player = await s.scalar(
            select(Player)
            .where(Player.club_id == club_id, Player.position != "GK")
            .order_by(Player.person_id)
            .limit(1)
        )
        pid = player.person_id
        owner = await s.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        person = await s.get(Person, pid)
        face = (person.appearance_identity_id, person.age)
        salary = await s.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == pid, SalaryContract.ended_at.is_(None)
            )
        )
        rate = salary.weekly_rate
        quote = await quote_release(s, action(actor), owner.id, pid, clock=lambda: NOW)
        assert quote.compensation == rate
        balance = (await s.get(Club, club_id)).budget
        qid, oid = quote.id, owner.id

    async def confirm():
        async with sessions.begin() as s:
            return await confirm_release(s, action(actor), qid, clock=lambda: NOW)

    both = await asyncio.wait_for(asyncio.gather(confirm(), confirm()), 20)
    assert both[0].confirmed_at == both[1].confirmed_at
    async with sessions.begin() as s:
        assert (await s.get(Club, club_id)).budget == balance - rate
        assert (
            await s.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "player_release")
            )
            == 1
        )
        assert (await s.get(Player, pid)).club_id is None
        assert not await s.scalar(select(LineupSlot).where(LineupSlot.person_id == pid))
        assert (await s.get(SalaryContract, salary.id)).ended_at == NOW
        offer = await s.get(CandidateOffer, pid)
        assert offer.status == "available" and offer.fee > 0
        person = await s.get(Person, pid)
        assert (person.appearance_identity_id, person.age) == face
        assert await supply_count(s, "player", "player", NOW) >= 1
        signed = await sign_candidate(s, action(actor), oid, pid, offer.fee, at=NOW)
        secondquote = await quote_release(s, action(actor), oid, pid, clock=lambda: NOW)
        await confirm_release(s, action(actor), secondquote.id, clock=lambda: NOW)
        second = await sign_candidate(
            s, action(actor), oid, pid, (await s.get(CandidateOffer, pid)).fee, at=NOW
        )
        assert signed.operation_id != second.operation_id
        assert (
            await s.scalar(
                select(func.count())
                .select_from(CandidateSigning)
                .where(CandidateSigning.person_id == pid)
            )
            == 2
        )
        assert (await s.get(Player, pid)).club_id == club_id


async def test_release_guards_and_transaction_rollback(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as s:
        pid = await s.scalar(
            select(Player.person_id)
            .where(Player.club_id == club_id, Player.position != "GK")
            .limit(1)
        )
        oid = await s.scalar(
            select(OwnershipPeriod.id).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        quote = await quote_release(s, action(actor), oid, pid, clock=lambda: NOW)
        qid = quote.id
    async with sessions() as s:
        await s.begin()
        await confirm_release(s, action(actor), qid, clock=lambda: NOW)
        await s.flush()
        await s.rollback()
    async with sessions.begin() as s:
        assert (await s.get(Player, pid)).club_id == club_id
        assert (await s.get(PlayerRelease, qid)).confirmed_at is None
        with pytest.raises(ValueError, match="expired"):
            await confirm_release(
                s, replace(action(actor), at=NOW + timedelta(minutes=16)), qid, clock=lambda: NOW
            )
        with pytest.raises(PermissionDenied):
            await quote_release(s, action(actor), uuid4(), pid, clock=lambda: NOW)
        p = await s.get(Player, pid)
        p.last_season_id = (await s.get(Person, pid)).age_applied_for_season_id
        p.status = "last_season"
        with pytest.raises(ValueError, match="last season"):
            await confirm_release(s, action(actor), qid, clock=lambda: NOW)
        p.status, p.last_season_id = "active", None
