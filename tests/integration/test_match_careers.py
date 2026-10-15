"""Real minute accounting, atomic awards and private retirement offers on Goaletto DB."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Integer, delete, func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.candidates import load_candidates, replenish_pool, sign_candidate
from goaletto.application.finances import close_salary
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.history import get_match_report
from goaletto.application.outbox_delivery import Decision, check_recipient
from goaletto.application.results import finalize_result
from goaletto.application.staff_career import assign_career, decline_offer, expire_job
from goaletto.application.staff_changes import execute_change, quote_change, release_staff
from goaletto.bot.candidate_views import candidate_list, staff_change_view
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.staff_career import loyal_wage, retirement_career
from goaletto.infrastructure.models import (
    CandidateOffer,
    CandidatePool,
    CandidateSigning,
    Club,
    Fixture,
    Job,
    LineupSlot,
    Match,
    MatchDevelopment,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    Player,
    PlayerDevelopment,
    SalaryContract,
    Staff,
    StaffCareer,
    StaffQualification,
)
from tests.integration.test_agent_wave import NOW, job, start_fixture
from tests.integration.test_candidates import action, funded
from tests.integration.test_live_commands import accept, live

pytestmark = pytest.mark.integration


async def retire(session, actor, club, *, role=None, owned=True):
    players = (await session.scalars(select(Player).where(Player.club_id == club))).all()
    person = None
    for player in players:
        candidate = await session.get(Person, player.person_id)
        if (
            role is None
            or retirement_career(candidate.id, candidate.age_applied_for_season_id)[0] == role
        ):
            person = candidate
            break
    assert person is not None
    player = await session.get(Player, person.id)
    season_id = person.age_applied_for_season_id
    await close_salary(session, person.id, NOW)
    await session.flush()
    await session.execute(delete(LineupSlot).where(LineupSlot.person_id == person.id))
    person.age = 36
    player.club_id, player.status = None, "retired"
    player.last_season_id = player.retired_after_season_id = season_id
    person.career = "retired"
    await session.flush()
    career = await assign_career(session, person, season_id, club if owned else None, NOW)
    return person, career


async def test_real_runtime_minutes_both_sides_staff_pin_retry_and_frozen_report(sessions):
    actor, fixture_id, at, screen = await live(sessions)
    from goaletto.application.live_commands import load_live_screen

    for tick in range(1, 6):
        at = await job(sessions, "match.tick", fixture_id, tick)
    actor = replace(action(actor), at=at)
    async with sessions.begin() as session:
        screen = await load_live_screen(session, actor, clock=lambda: at, record=False)
    team = screen.state.home if screen.side.value == "home" else screen.state.away
    outgoing, incoming = team.lineup[1].player_id, team.bench[0]
    await accept(
        sessions,
        actor,
        screen,
        "substitution",
        {"outgoing_id": str(outgoing), "incoming_id": str(incoming)},
    )
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        original = state_hash(restore_state(match.starting_snapshot))
        pinned = (await session.get(MatchDevelopment, match.id)).staff_snapshot
        assert pinned[str(team.snapshot.club_id)]["head_coach"]["skill"] == 13
        coach = await session.scalar(
            select(StaffQualification)
            .join(Staff)
            .where(Staff.club_id == team.snapshot.club_id, StaffQualification.role == "head_coach")
        )
        coach.skill = 20
        for player in (*screen.state.home.snapshot.players, *screen.state.away.snapshot.players):
            dev = await session.get(PlayerDevelopment, player.id)
            dev.caps = dict.fromkeys(dev.caps, 20)
    for tick in range(6, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture_id)
        await job(sessions, "match.tick", fixture_id, tick)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        receipt = await session.get(MatchDevelopment, match.id)
        state = restore_state(match.state)
        assert receipt.applied_at and receipt.eligible and receipt.staff_snapshot == pinned
        assert state_hash(restore_state(match.starting_snapshot)) == original
        minutes = {
            c.player_id: c.minutes_played for t in (state.home, state.away) for c in t.conditions
        }
        rows = {r["person_id"]: r for r in receipt.results}
        assert all(rows[str(pid)]["minutes"] == mins for pid, mins in minutes.items() if mins)
        assert all(str(pid) not in rows for pid, mins in minutes.items() if not mins)
        assert minutes[outgoing] == 15 and 0 < minutes[incoming] <= 75
        assert {r["club_id"] for r in rows.values()} == {
            str(state.home.snapshot.club_id),
            str(state.away.snapshot.club_id),
        }
        assert all(r["experience_gain"] > 0 for r in rows.values())
        report = await get_match_report(session, action(actor), fixture_id)
        assert any(p.growth_bps is not None for p in (*report.home_lineup, *report.away_lineup))
        before = list(receipt.results)
        assert not await finalize_result(
            session, fixture_id, match.next_due_at or at + timedelta(hours=1)
        )
        assert receipt.results == before
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE match_development SET results='[]'::jsonb WHERE match_id=:id"),
                {"id": screen.match_id},
            )


async def test_private_career_replaces_role_and_discount_survives_dismissal_rehire(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        person, career = await retire(session, actor, club, role="head_coach")
        identity = (person.age, person.appearance_identity_id, person.name)
        again = await assign_career(session, person, career.season_id, club, NOW)
        assert again.person_id == person.id and again.role == career.role
        screen = await load_candidates(session, action(actor), "staff")
        item = next(i for i in screen.items if i.person.id == person.id)
        assert item.loyal and item.private_until and item.wage == loyal_wage(item.offer.weekly_rate)
        assert "Лояльность клубу" in candidate_list(screen, "staff")[0]
        quote = await quote_change(
            session,
            action(actor),
            screen.ownership_id,
            incoming_id=person.id,
            quoted_fee=item.offer.fee,
        )
        assert quote.outgoing_id is not None
        assert "Лояльность клубу" in staff_change_view(quote)[0]
        request = action(actor)
        await execute_change(session, request, quote.id, at=NOW)
        await execute_change(session, request, quote.id, at=NOW)
        first = await session.get(CandidateSigning, request.operation_id)
        assert first.weekly_rate == item.wage and first.fee == item.offer.fee
        assert (await session.get(StaffCareer, person.id)).status == "accepted"
        await release_staff(session, person.id, club, NOW + timedelta(hours=1))
        offer = await session.get(CandidateOffer, person.id)
        later = replace(action(actor), at=NOW + timedelta(hours=2))
        q = await quote_change(
            session, action(later), screen.ownership_id, incoming_id=person.id, quoted_fee=offer.fee
        )
        await execute_change(session, later, q.id, at=later.at)
        salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == person.id, SalaryContract.ended_at.is_(None)
            )
        )
        assert salary.weekly_rate == loyal_wage(offer.weekly_rate)
        assert (person.age, person.appearance_identity_id, person.name) == identity
        assert (await session.get(Player, person.id)).status == "retired"


async def test_private_offer_is_hidden_from_other_club_then_decline_preserves_public_surplus(
    sessions,
):
    from goaletto.application.onboarding import begin_game, claim_club

    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        person, career = await retire(session, actor, club)
        screen = await load_candidates(session, action(actor), "staff")
        pool = await session.get(CandidatePool, ("staff", career.role))
        generation = pool.generation
        entry = await begin_game(session, telegram_id=1002, update_id=99, payload=None, at=NOW)
        other = action(entry.actor)
        await claim_club(session, other, entry.offers[0].id)
        foreign = await load_candidates(session, action(other), "staff")
        assert person.id not in {i.person.id for i in foreign.items}
        offer = await session.get(CandidateOffer, person.id)
        with pytest.raises(ValueError, match="Private"):
            await sign_candidate(
                session, action(other), foreign.ownership_id, person.id, offer.fee, at=NOW
            )
        with pytest.raises(PermissionDenied):
            await decline_offer(session, action(other), person.id, foreign.ownership_id)
        message = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.payload["kind"].astext == "staff_career")
        )
        assert await check_recipient(session, message, NOW) == Decision.DELIVER
        await decline_offer(session, action(actor), person.id, screen.ownership_id)
        await decline_offer(session, action(actor), person.id, screen.ownership_id)
        assert await check_recipient(session, message, NOW) == Decision.SKIP_STALE
        assert await replenish_pool(session, "staff", career.role, NOW) == 0
        assert pool.generation == generation
        public = await load_candidates(session, action(other), "staff")
        item = next(i for i in public.items if i.person.id == person.id)
        assert not item.loyal and item.wage == offer.weekly_rate
        await sign_candidate(
            session, action(other), public.ownership_id, person.id, offer.fee, at=NOW
        )
        salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == person.id, SalaryContract.ended_at.is_(None)
            )
        )
        assert salary.weekly_rate == offer.weekly_rate


async def test_expiry_job_and_owner_release_make_offers_public_without_automatic_hire(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        person, career = await retire(session, actor, club)
        work = await session.scalar(select(Job).where(Job.kind == "staff.career_expire"))
        assert work.due_at == NOW + timedelta(days=8)
        await expire_job(session, work, work.due_at)
        await expire_job(session, work, work.due_at)
        assert career.status == "public" and (await session.get(Staff, person.id)).club_id is None
        another, outcome = await retire(session, actor, club)
        screen = await load_candidates(session, action(actor), "staff")
        owner = await session.get(OwnershipPeriod, screen.ownership_id)
        owner.ended_at, owner.end_reason = NOW, "voluntary"
        (await session.get(Club, club)).owner_telegram_id = None
        from goaletto.application.staff_career import expire_offers

        await expire_offers(session, NOW)
        assert outcome.status == "public"
        assert (await session.get(Staff, another.id)).club_id is None


async def test_career_accept_versus_decline_is_serialized(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        person, career = await retire(session, actor, club)
        screen = await load_candidates(session, action(actor), "staff")
        offer = await session.get(CandidateOffer, person.id)
        q = await quote_change(
            session, action(actor), screen.ownership_id, incoming_id=person.id, quoted_fee=offer.fee
        )

    async def accept_offer():
        try:
            async with sessions.begin() as session:
                await execute_change(session, action(actor), q.id, at=NOW)
        except ValueError:
            pass

    async def decline():
        try:
            async with sessions.begin() as session:
                await decline_offer(session, action(actor), person.id, screen.ownership_id)
        except ValueError:
            pass

    await asyncio.wait_for(asyncio.gather(accept_offer(), decline()), 20)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(StaffCareer)) == 1
        current = await session.get(StaffCareer, person.id)
        staff = await session.get(Staff, person.id)
        # A released public candidate can subsequently be hired at the same home terms.
        assert (await session.get(CandidateOffer, person.id)).status == (
            "signed" if staff.club_id else "available"
        )
        assert current.status in {"public", "accepted"}


async def test_operator_fixture_without_announced_plan_cannot_grant_development(sessions):
    from goaletto.application.match_development import apply_development, pin_development
    from goaletto.application.match_runtime import team_snapshot
    from goaletto.application.match_simulation import simulate_match
    from goaletto.domain.match_codec import state_document
    from goaletto.domain.match_engine import start_match
    from goaletto.infrastructure.models import FixturePlan

    result, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        planned = await session.scalar(
            select(FixturePlan).where(
                FixturePlan.season_id == result.season_id,
                FixturePlan.competition == "league",
                FixturePlan.home_club_id == club,
                FixturePlan.announced_at.is_(None),
            )
        )
        assert planned is not None
        fixture = Fixture(
            id=uuid4(),
            season_id=planned.season_id,
            league_id=planned.league_id,
            home_club_id=planned.home_club_id,
            away_club_id=planned.away_club_id,
            round_number=planned.round_number,
            scheduled_start_at=NOW,
            lineup_lock_at=NOW,
            status="scheduled",
        )
        session.add(fixture)
        snapshots = [
            await team_snapshot(session, await session.get(Club, cid), NOW)
            for cid in (fixture.home_club_id, fixture.away_club_id)
        ]
        initial = start_match(uuid4(), 42, *snapshots, engine_version="regulation-v3")
        final, _ = simulate_match(initial)
        match = Match(
            id=initial.match_id,
            fixture_id=fixture.id,
            seed=42,
            engine_version=initial.engine_version,
            state_version=3,
            state=state_document(initial),
            starting_snapshot=state_document(initial),
            completed_tick=0,
            phase="first_half",
            home_goals=0,
            away_goals=0,
        )
        session.add(match)
        await session.flush()
        await pin_development(session, fixture, initial, NOW)
        await session.flush()
        await apply_development(session, final, NOW + timedelta(hours=1))
        receipt = await session.get(MatchDevelopment, match.id)
        assert not receipt.eligible and receipt.applied_at and receipt.results == []
        assert not await session.scalar(
            select(PlayerDevelopment.person_id).where(
                PlayerDevelopment.season_gains["match"].astext.cast(Integer) > 0
            )
        )


async def test_last_tick_failure_rolls_back_awards_and_finalization_then_retries(
    sessions, monkeypatch
):
    import goaletto.application.match_development as module

    _, actor, club = await funded(sessions)
    fixture, at = await start_fixture(sessions, club)
    for tick in range(1, 30):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture)
        await job(sessions, "match.tick", fixture, tick)
    original = module.apply_development

    async def fail(session, state, at):
        await original(session, state, at)
        await session.flush()
        raise RuntimeError("after appearance awards")

    monkeypatch.setattr(module, "apply_development", fail)
    with pytest.raises(RuntimeError, match="after appearance"):
        await job(sessions, "match.tick", fixture, 30)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        receipt = await session.get(MatchDevelopment, match.id)
        assert match.completed_tick == 29 and receipt.applied_at is None and receipt.results == []
        assert not await session.scalar(
            select(PlayerDevelopment.person_id).where(
                PlayerDevelopment.season_gains["match"].astext.cast(Integer) > 0
            )
        )
    monkeypatch.setattr(module, "apply_development", original)
    await job(sessions, "match.tick", fixture, 30)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        receipt = await session.get(MatchDevelopment, match.id)
        assert receipt.applied_at and len(receipt.results) >= 22
