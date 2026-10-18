"""Staff changes and pinned coach budgets on the single development Goaletto DB."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.candidates import load_candidates, sign_candidate, supply_count
from goaletto.application.coaches import coach_snapshot, ensure_starting_coaches
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.lineup import LineupInput, load_lineup, save_lineup
from goaletto.application.live_commands import (
    CommandUnavailable,
    accept_live_command,
    load_live_screen,
)
from goaletto.application.money import credit_locked, locked_accounts
from goaletto.application.staff_changes import HeadCoachRequired, execute_change, quote_change
from goaletto.domain.finances import accrued_wage
from goaletto.domain.lineup import LineupInvalid, StarterPick
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.infrastructure.models import (
    CandidateOffer,
    CandidatePool,
    CandidateSigning,
    ClubLineup,
    LedgerEntry,
    Match,
    OwnershipPeriod,
    Person,
    SalaryContract,
    Staff,
    StaffChange,
    StaffQualification,
)
from tests.integration.test_agent_wave import NOW, job, start_fixture
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def offer_for(session, actor, role="head_coach"):
    screen = await load_candidates(session, action(actor), "staff")
    item = next(i for i in screen.items if i.offer.profile == role)
    return screen, item


async def quote_for(session, actor, item, owner):
    return await quote_change(
        session, action(actor), owner, incoming_id=item.person.id, quoted_fee=item.offer.fee
    )


async def test_baseline_backfill_only_missing_preserves_other_staff_and_retries(
    sessions, monkeypatch
):
    with monkeypatch.context() as patch:
        patch.setattr(
            "goaletto.application.coaches.ensure_starting_coaches", AsyncMock(return_value=0)
        )
        _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen, item = await offer_for(session, actor, "goalkeeping_coach")
        await sign_candidate(
            session, action(actor), screen.ownership_id, item.person.id, item.offer.fee, at=NOW
        )
        salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == item.person.id, SalaryContract.ended_at.is_(None)
            )
        )
        salary_id, rate = salary.id, salary.weekly_rate
        assert await ensure_starting_coaches(session, NOW) == 50
        assert await ensure_starting_coaches(session, NOW) == 0
        assert (await coach_snapshot(session, club_id)).skill == 1
        assert (
            await session.scalar(
                select(func.count()).select_from(Staff).where(Staff.role == "head_coach")
            )
            == 50
        )
        assert (await session.get(Staff, item.person.id)).club_id == club_id
        salary = await session.get(SalaryContract, salary_id)
        assert salary.weekly_rate == rate and salary.ended_at is None


async def test_replace_returns_same_person_and_rehire_without_rejuvenation(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        old = await coach_snapshot(session, club_id)
        old_person = await session.get(Person, old.person_id)
        identity, age = old_person.appearance_identity_id, old_person.age
        old_salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == old.person_id, SalaryContract.ended_at.is_(None)
            )
        )
        salary_id, old_rate = old_salary.id, old_salary.weekly_rate
        screen, item = await offer_for(session, actor)
        quote = await quote_for(session, actor, item, screen.ownership_id)
        generation = (await session.get(CandidatePool, ("staff", "head_coach"))).generation
        quote_id = quote.id
    at = NOW + timedelta(days=1)
    async with sessions.begin() as session:
        await execute_change(session, replace(action(actor), at=at), quote_id, at=at)
        # Repeated callback, even another Telegram update, never debits or swaps again.
        await execute_change(session, replace(action(actor), at=at), quote_id, at=at)
        assert (await session.get(SalaryContract, salary_id)).ended_at == at
        assert accrued_wage(old_rate, 86400, 345600) > 0
        assert await supply_count(session, "staff", "head_coach", at) == 2
        assert (await session.get(CandidatePool, ("staff", "head_coach"))).generation == generation
        previous = await session.get(Person, old.person_id)
        assert (previous.age, previous.appearance_identity_id) == (age, identity)
        offer = await session.get(CandidateOffer, previous.id)
        assert offer.status == "available"
        again = await quote_change(
            session,
            replace(action(actor), at=at),
            screen.ownership_id,
            incoming_id=previous.id,
            quoted_fee=offer.fee,
        )
        await execute_change(
            session,
            replace(action(actor), at=at + timedelta(hours=1)),
            again.id,
            at=at + timedelta(hours=1),
        )
        # A second employment of the same person uses another receipt, keeping both histories.
        assert (await session.get(Staff, previous.id)).club_id == club_id
        current = await session.get(CandidateOffer, item.person.id)
        quote = await quote_change(
            session,
            replace(action(actor), at=at + timedelta(hours=2)),
            screen.ownership_id,
            incoming_id=item.person.id,
            quoted_fee=current.fee,
        )
        await execute_change(
            session,
            replace(action(actor), at=at + timedelta(hours=2)),
            quote.id,
            at=at + timedelta(hours=2),
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CandidateSigning)
                .where(CandidateSigning.person_id == item.person.id)
            )
            == 2
        )


async def test_parallel_replacements_and_stale_quote_preserve_single_job(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "staff")
        items = [i for i in screen.items if i.offer.profile == "head_coach"]
        quotes = [await quote_for(session, actor, item, screen.ownership_id) for item in items]

    async def change(quote):
        try:
            async with sessions.begin() as session:
                await execute_change(session, action(actor), quote.id, at=NOW)
            return True
        except ValueError:
            return False

    assert sum(await asyncio.wait_for(asyncio.gather(*(change(q) for q in quotes)), 30)) == 1
    async with sessions.begin() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Staff)
                .where(Staff.club_id == club_id, Staff.role == "head_coach")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "signing_fee")
            )
            == 1
        )


async def test_unaffordable_replace_and_interrupted_transaction_keep_old_contract(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen, item = await offer_for(session, actor)
        item.offer.fee = 3_000_000
        quote = await quote_for(session, actor, item, screen.ownership_id)
        old_id = quote.outgoing_id
    with pytest.raises(ValueError):
        async with sessions.begin() as session:
            await execute_change(session, action(actor), quote.id, at=NOW)
    async with sessions.begin() as session:
        assert (await session.get(Staff, old_id)).club_id == club_id
        assert (await session.get(StaffChange, quote.id)).executed_at is None
        assert (await session.get(CandidateOffer, item.person.id)).status == "available"
        account = (await locked_accounts(session, club_id))[club_id]
        await credit_locked(session, account, "rollback-test-grant", "grant", 3_000_000, NOW)
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await execute_change(session, action(actor), quote.id, at=NOW)
            raise RuntimeError("interrupted before commit")
    async with sessions.begin() as session:
        assert (await session.get(Staff, old_id)).club_id == club_id
        assert (await session.get(StaffChange, quote.id)).executed_at is None
        assert await session.scalar(
            select(SalaryContract.id).where(
                SalaryContract.person_id == old_id, SalaryContract.ended_at.is_(None)
            )
        )


async def test_coach_dismissal_is_blocked_but_last_season_replacement_and_rehire_work(sessions):
    result, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        coach = await coach_snapshot(session, club_id)
        staff = await session.get(Staff, coach.person_id)
        staff.status, staff.last_season_id = "last_season", result.season_id
        owner = await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        salary_id = await session.scalar(
            select(SalaryContract.id).where(
                SalaryContract.person_id == coach.person_id, SalaryContract.ended_at.is_(None)
            )
        )
        ledger_count = await session.scalar(select(func.count()).select_from(LedgerEntry))
        with pytest.raises(HeadCoachRequired):
            await quote_change(session, action(actor), owner.id, outgoing_id=coach.person_id)
        # Persisted pre-rule confirmations must be rejected on execution too.
        legacy = StaffChange(
            id=uuid4(),
            club_id=club_id,
            ownership_id=owner.id,
            role="head_coach",
            outgoing_id=coach.person_id,
            incoming_id=None,
            terms={},
            created_at=NOW,
        )
        session.add(legacy)
        await session.flush()
        with pytest.raises(HeadCoachRequired):
            await execute_change(session, action(actor), legacy.id, at=NOW)
        assert legacy.executed_at is None
        assert (await session.get(SalaryContract, salary_id)).ended_at is None
        assert staff.club_id == club_id
        assert ledger_count == await session.scalar(select(func.count()).select_from(LedgerEntry))
        screen, item = await offer_for(session, actor)
        quote = await quote_for(session, actor, item, screen.ownership_id)
        await execute_change(session, action(actor), quote.id, at=NOW)
        assert staff.status == "last_season" and staff.last_season_id == result.season_id
        assert (await coach_snapshot(session, club_id)).person_id == item.person.id
        assert await ensure_starting_coaches(session, NOW) == 0
        screen = await load_candidates(session, action(actor), "staff")
        assert any(i.person.id == coach.person_id for i in screen.items)
        offer = await session.get(CandidateOffer, coach.person_id)
        assert offer.weekly_rate > 0
        rehiring = await quote_change(
            session, action(actor), owner.id, incoming_id=coach.person_id, quoted_fee=offer.fee
        )
        await execute_change(session, action(actor), rehiring.id, at=NOW)
        assert staff.status == "last_season" and staff.last_season_id == result.season_id
        assert (await coach_snapshot(session, club_id)).person_id == coach.person_id


async def test_coach_pre_match_admission_pending_limits_and_snapshot_survive_new_hire(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        data = await load_lineup(session, action(actor))
        request = LineupInput(
            tuple(StarterPick(m.person_id, m.slot_position) for m in data.starters),
            tuple(m.person_id for m in data.bench),
            data.formation,
            data.starters[1].person_id,
            "pressing",
            "balanced",
            data.version,
        )
    with pytest.raises(LineupInvalid):
        async with sessions.begin() as session:
            await save_lineup(session, action(actor), request, clock=lambda: NOW)
    fixture_id, at = await start_fixture(sessions, club_id)
    actor = replace(actor, at=at)
    async with sessions.begin() as session:
        screen = await load_live_screen(session, action(actor), clock=lambda: at, record=False)
        original = state_hash(
            restore_state((await session.get(Match, screen.match_id)).starting_snapshot)
        )

        async def command(kind, payload):
            return await accept_live_command(
                session,
                action(actor),
                match_id=screen.match_id,
                ownership_id=screen.ownership_id,
                target=1,
                halftime=False,
                kind=kind,
                payload=payload,
                clock=lambda: at,
            )

        await command("mentality", {"mentality": "attack"})
        with pytest.raises(CommandUnavailable, match="mentality_limit"):
            await command("mentality", {"mentality": "defence"})
        with pytest.raises(CommandUnavailable, match="tactic_unavailable"):
            await command("tactics", {"tactic": "pressing"})
        team = screen.state.home if screen.side.value == "home" else screen.state.away
        await command(
            "instruction", {"player_id": str(team.lineup[1].player_id), "instruction": "long_shots"}
        )
        with pytest.raises(CommandUnavailable, match="instruction_limit"):
            await command(
                "instruction",
                {"player_id": str(team.lineup[2].player_id), "instruction": "drop_back"},
            )
        market, item = await offer_for(session, actor)
        (await session.get(StaffQualification, (item.person.id, "head_coach"))).skill = 20
        quote = await quote_for(session, actor, item, market.ownership_id)
        await execute_change(session, action(actor), quote.id, at=at)
        assert (await coach_snapshot(session, club_id)).skill == 20
        with pytest.raises(CommandUnavailable, match="mentality_limit"):
            await command("mentality", {"mentality": "defence"})
    await job(sessions, "match.tick", fixture_id, 1)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        state = restore_state(match.state)
        team = state.home if screen.side.value == "home" else state.away
        assert (team.mentality_changes, team.instruction_changes, team.snapshot.coach.skill) == (
            1,
            1,
            1,
        )
        assert state_hash(restore_state(match.starting_snapshot)) == original
        data = await session.get(ClubLineup, club_id)
        assert data.tactics.get("tactic", "normal") == "normal"


async def test_staff_quote_rejects_changed_price_and_former_owner(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen, item = await offer_for(session, actor)
        quote = await quote_for(session, actor, item, screen.ownership_id)
        (await session.get(CandidateOffer, item.person.id)).weekly_rate += 1
    with pytest.raises(ValueError, match="terms changed"):
        async with sessions.begin() as session:
            await execute_change(session, action(actor), quote.id, at=NOW)
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await execute_change(
                session,
                replace(action(actor), at=NOW + timedelta(days=200)),
                quote.id,
                at=NOW + timedelta(days=200),
            )


async def test_dismissing_scout_cancels_paid_mission_in_same_transaction(sessions):
    from goaletto.application.scouting import complete_search, start_search
    from goaletto.domain.finances import scout_mission_fee
    from goaletto.infrastructure.models import Job, ScoutDiscovery, ScoutMission
    from tests.integration.test_scouting import ready

    actor, club_id, owner, scout_id, skill = await ready(sessions)
    async with sessions.begin() as session:
        mission = await start_search(
            session, action(actor), owner, scout_id, "FC", 4, scout_mission_fee(skill, 4)
        )
        quote = await quote_change(session, action(actor), owner, outgoing_id=scout_id)
        budget = (await locked_accounts(session, club_id))[club_id].available
        await execute_change(session, action(actor), quote.id, at=NOW)
        assert (await session.get(ScoutMission, mission.id)).status == "cancelled"
        assert (await locked_accounts(session, club_id))[club_id].available == budget
    async with sessions.begin() as session:
        pending = await session.scalar(select(Job).where(Job.kind == "scout.complete"))
        await complete_search(session, pending, NOW + timedelta(days=4))
        assert await session.scalar(select(func.count()).select_from(ScoutDiscovery)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "scout_search")
            )
            == 1
        )


async def test_coach_retirement_closes_salary_and_restores_basic_future_tactic(sessions):
    from goaletto.application.season_rollover import _rollover_staff
    from goaletto.infrastructure.models import Season, SeasonClub

    result, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        coach = await coach_snapshot(session, club_id)
        staff = await session.get(Staff, coach.person_id)
        staff.status, staff.last_season_id = "last_season", result.season_id
        lineup = await session.get(ClubLineup, club_id)
        lineup.tactics = {"tactic": "pressing", "mentality": "balanced"}
        season = await session.get(Season, result.season_id)
        successor = Season(
            id=uuid4(),
            league_set_id=season.league_set_id,
            number=2,
            seed=0,
            starts_at=season.ends_at + timedelta(days=8),
            ends_at=season.ends_at + timedelta(days=80),
            status="scheduled",
        )
        session.add(successor)
        await session.flush()
        members = {
            m.club_id: m
            for m in (
                await session.scalars(select(SeasonClub).where(SeasonClub.season_id == season.id))
            ).all()
        }
        counts = {key: 0 for key in ("staff_aged", "staff_warned", "staff_retired")}
        await _rollover_staff(
            session,
            season,
            successor.id,
            0,
            members,
            season.ends_at,
            counts,
            {member: successor.id for member in members},
        )
        assert staff.status == "retired" and counts["staff_retired"] == 1
        assert (await coach_snapshot(session, club_id)).person_id is None
        assert lineup.tactics["tactic"] == "normal"
        assert not await session.scalar(
            select(SalaryContract.id).where(
                SalaryContract.person_id == coach.person_id, SalaryContract.ended_at.is_(None)
            )
        )
        assert await ensure_starting_coaches(session, season.ends_at) == 0


async def test_replacement_invoice_charges_only_actual_old_and_new_intervals(sessions):
    from goaletto.application.finances import cycle_finances
    from goaletto.infrastructure.models import FinanceCycle, Job

    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        invoice = await session.scalar(
            select(Job).where(
                Job.kind == "finance.cycle", Job.payload["club_id"].astext == str(club_id)
            )
        )
        at = NOW + (invoice.due_at - NOW) / 2
        old = await coach_snapshot(session, club_id)
        old_salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == old.person_id, SalaryContract.ended_at.is_(None)
            )
        )
        screen, item = await offer_for(session, actor)
        quote = await quote_for(session, actor, item, screen.ownership_id)
        await execute_change(session, replace(action(actor), at=at), quote.id, at=at)
        new_salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == item.person.id, SalaryContract.ended_at.is_(None)
            )
        )
        await cycle_finances(session, invoice, invoice.due_at)
        cycle = await session.scalar(select(FinanceCycle).where(FinanceCycle.club_id == club_id))
        saved = {row["id"]: row for row in cycle.snapshot["contracts"]}
        assert saved[str(old_salary.id)]["seconds"] == int((at - NOW).total_seconds())
        assert saved[str(new_salary.id)]["seconds"] == int((invoice.due_at - at).total_seconds())
        assert saved[str(old_salary.id)]["due"] == accrued_wage(
            old_salary.weekly_rate, saved[str(old_salary.id)]["seconds"]
        )
        assert saved[str(new_salary.id)]["due"] == accrued_wage(
            new_salary.weekly_rate, saved[str(new_salary.id)]["seconds"]
        )
        assert (
            cycle.snapshot["amounts"]["staff_salary"]
            == saved[str(old_salary.id)]["due"] + saved[str(new_salary.id)]["due"]
        )
        balance = (await locked_accounts(session, club_id))[club_id].available
        await cycle_finances(session, invoice, invoice.due_at)
        assert (await locked_accounts(session, club_id))[club_id].available == balance
