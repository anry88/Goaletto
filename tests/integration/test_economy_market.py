"""Economy versions survive hiring, mutable skills, mixed accrual and replay."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.candidates import load_candidates, sign_candidate
from goaletto.application.finances import close_salary, cycle_finances, salary_contract
from goaletto.domain.finances import POLICY_VERSION, player_fee, player_wage, staff_fee, staff_wage
from goaletto.infrastructure.models import (
    CandidateSigning,
    FinanceCycle,
    Job,
    LedgerEntry,
    Person,
    Player,
    PlayerDevelopment,
    SalaryContract,
    StaffQualification,
)
from tests.integration.test_candidates import NOW, action, funded

pytestmark = pytest.mark.integration


async def test_public_adults_have_stable_caps_and_exact_quotes_through_purchase(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "player")
        assert len(screen.items) == 12
        assert all(26 <= i.person.age <= 30 for i in screen.items)
        assert len({i.offer.fee for i in screen.items}) > 3
        item = screen.items[0]
        fee, wage = item.offer.fee, item.offer.weekly_rate
        person_id, age, face = item.person.id, item.person.age, item.person.appearance_identity_id
        assert fee == player_fee(item.skills, age, item.offer.profile, 1 + len(item.alternatives))
        assert wage == player_wage(item.skills, age, item.offer.profile, 1 + len(item.alternatives))
        caps = dict((await session.get(PlayerDevelopment, person_id)).caps)
        assert set(caps.values()) != {20}
        # A quote is a contract snapshot; later skills never change its wage or price.
        player = await session.get(Player, person_id)
        player.pace = min(20, player.pace + 1)
        request = action(actor)
        receipt = await sign_candidate(
            session, request, screen.ownership_id, person_id, fee, at=NOW
        )
        assert (receipt.fee, receipt.weekly_rate, receipt.economy_policy) == (
            fee,
            wage,
            POLICY_VERSION,
        )
        rate = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == person_id, SalaryContract.ended_at.is_(None)
            )
        )
        assert (rate.weekly_rate, rate.policy_version) == (wage, POLICY_VERSION)
        assert (await session.get(Person, person_id)).appearance_identity_id == face
        assert (await session.get(Person, person_id)).age == age
        assert (await session.get(PlayerDevelopment, person_id)).caps == caps
        assert (
            await sign_candidate(session, request, screen.ownership_id, person_id, fee, at=NOW)
        ).operation_id == receipt.operation_id
        assert await session.scalar(select(func.count()).select_from(CandidateSigning)) == 1
        entry = await session.scalar(select(LedgerEntry).where(LedgerEntry.kind == "signing_fee"))
        assert entry.request["details"]["policy"] == POLICY_VERSION
        assert entry.cash_delta == -fee


async def test_grandfathered_unsigned_offer_keeps_age_face_and_original_terms(sessions):
    _, actor, _ = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "player")
        item = screen.items[0]
        offer = item.offer
        offer.fee, offer.weekly_rate, offer.economy_policy = 100000, 1010, "economy-cycle-v1"
        item.person.age = 18  # Construct an already-created prototype offer.
        face = item.person.appearance_identity_id
        await session.flush()
        again = await load_candidates(session, action(actor), "player")
        saved = next(i for i in again.items if i.person.id == item.person.id)
        assert (saved.offer.fee, saved.offer.weekly_rate, saved.person.age) == (100000, 1010, 18)
        receipt = await sign_candidate(
            session, action(actor), screen.ownership_id, item.person.id, 100000, at=NOW
        )
        rate = await session.scalar(
            select(SalaryContract).where(SalaryContract.person_id == item.person.id)
        )
        assert rate.weekly_rate == receipt.weekly_rate == 1010
        assert rate.policy_version == receipt.economy_policy == "economy-cycle-v1"
        assert item.person.age == 18 and item.person.appearance_identity_id == face
        assert all(
            26 <= i.person.age <= 30
            for i in (await load_candidates(session, action(actor), "player")).items
        )


async def test_staff_actual_age_qualification_quote_and_mixed_salary_invoice(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "staff")
        item = next(i for i in screen.items if i.offer.profile == "scout")
        qualification = await session.get(StaffQualification, (item.person.id, "scout"))
        assert item.offer.fee == staff_fee("scout", qualification.skill, item.person.age)
        assert item.offer.weekly_rate == staff_wage("scout", qualification.skill, item.person.age)
        receipt = await sign_candidate(
            session, action(actor), screen.ownership_id, item.person.id, item.offer.fee, at=NOW
        )
        original = await session.scalar(
            select(SalaryContract)
            .where(SalaryContract.club_id == club_id, SalaryContract.role == "player")
            .limit(1)
        )
        await close_salary(session, original.person_id, NOW + timedelta(hours=1))
        old = await salary_contract(
            session,
            original.person_id,
            club_id,
            "player",
            NOW + timedelta(hours=1),
            quoted_player_wage=1234,
            policy_version="economy-cycle-v1",
        )
        job = await session.scalar(
            select(Job).where(
                Job.kind == "finance.cycle", Job.payload["club_id"].astext == str(club_id)
            )
        )
        job.status = "done"
        await cycle_finances(session, job, job.due_at)
        invoice = await session.scalar(select(FinanceCycle).where(FinanceCycle.club_id == club_id))
        assert invoice.policy_version == POLICY_VERSION
        rates = {c["id"]: c for c in invoice.snapshot["contracts"]}
        assert rates[str(old.id)]["rate"] == 1234
        assert rates[str(old.id)]["policy"] == "economy-cycle-v1"
        salary = await session.scalar(
            select(SalaryContract).where(SalaryContract.person_id == item.person.id)
        )
        assert rates[str(salary.id)]["rate"] == receipt.weekly_rate
        assert rates[str(salary.id)]["policy"] == POLICY_VERSION
        before = await session.scalar(select(func.count()).select_from(LedgerEntry))
        await cycle_finances(session, job, job.due_at + timedelta(days=1))
        assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == before
        # Hiring a scout does not schedule a costly mission.
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind.like("scout.%"))
            )
            == 0
        )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE salary_contracts SET weekly_rate=999 WHERE id=:id"), {"id": old.id}
            )
