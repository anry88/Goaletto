"""Start conditions for an inherited club, with immutable pre-claim records."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.academy import ensure_prospects, upgrade_academy
from goaletto.application.game_actions import end_expired
from goaletto.application.money import credit_locked, locked_accounts
from goaletto.application.onboarding import ClubUnavailable, begin_game, claim_club, preview_club
from goaletto.application.stadium import complete_expansion, order_expansion, upkeep_for_period
from goaletto.application.supporters import apply_season, supporters_at
from goaletto.infrastructure.models import (
    AcademyProspect,
    CandidateOffer,
    Club,
    ClubClaimReset,
    Fixture,
    Job,
    LedgerEntry,
    LineupSlot,
    OwnershipPeriod,
    Person,
    Player,
    SalaryContract,
    Season,
    StadiumExpansion,
    Staff,
    StaffQualification,
)
from tests.integration.test_agent_wave import NOW, setup_world, start_fixture

pytestmark = pytest.mark.integration


async def test_reclaim_resets_assets_and_preserves_people_and_finance_history(sessions):
    world, former, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        club = await session.get(Club, club_id)
        season = await session.get(Season, world.season_id)
        owner = await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        funds = (await locked_accounts(session, club_id))[club_id]
        await credit_locked(session, funds, "test-bot-income", "prize", 2_000_000, NOW)
        upgrade_at = NOW + timedelta(hours=1)
        await upgrade_academy(
            session,
            replace(former, at=upgrade_at, operation_id=uuid4()),
            owner.id,
            expected_level=1,
        )
        old_prospects = {p.id for p in await ensure_prospects(session, club, season)}
        assert len(old_prospects) == 4
        await order_expansion(
            session,
            replace(former, at=upgrade_at, operation_id=uuid4()),
            owner.id,
            5000,
            clock=lambda: upgrade_at,
        )
        work = await session.scalar(select(Job).where(Job.kind == "stadium.complete"))
        await complete_expansion(session, work, work.due_at)
        build_at = work.due_at + timedelta(minutes=1)
        pending = await order_expansion(
            session,
            replace(former, at=build_at, operation_id=uuid4()),
            owner.id,
            10000,
            clock=lambda: build_at,
        )
        await apply_season(session, club, world.season_id, 5, 3, 5, build_at)
        before_fans, before_cash = club.supporters, club.budget
        assert before_fans > 2500 and before_cash > 600_000
        old_players = {
            p.person_id: p.status
            for p in await session.scalars(select(Player).where(Player.club_id == club_id))
        }
        old_head = await session.scalar(select(Staff.person_id).where(Staff.club_id == club_id))
        claim_at = owner.expires_at
        assert await end_expired(session, owner, claim_at)
        new_entry = await begin_game(
            session, telegram_id=1002, update_id=10, payload=None, at=claim_at
        )
        preview = await preview_club(
            session, replace(new_entry.actor, operation_id=uuid4(), update_id=12), club_id
        )
        assert (preview.budget, preview.supporters, preview.stadium_capacity) == (
            600_000,
            2500,
            5000,
        )
        assert club.budget == before_cash
        actor = replace(new_entry.actor, operation_id=uuid4(), update_id=11)
        claimed = await claim_club(session, actor, club_id)
        assert (await claim_club(session, actor, club_id)).id == claimed.id
        receipt = await session.get(ClubClaimReset, claimed.id)
        assert (
            receipt.supporters_before,
            receipt.capacity_before,
            receipt.academy_level_before,
        ) == (before_fans, 10000, 2)
        assert (club.budget, club.supporters, club.stadium_capacity, club.academy_level) == (
            600_000,
            2500,
            5000,
            1,
        )
        assert (await session.get(StadiumExpansion, pending.id)).status == "cancelled"
        assert await supporters_at(session, club, claim_at - timedelta(seconds=1)) == before_fans
        assert await supporters_at(session, club, claim_at) == 2500
        _, periods = await upkeep_for_period(
            session, club, claim_at + timedelta(days=1), period_start=claim_at - timedelta(days=1)
        )
        assert [p["capacity"] for p in periods] == [10000, 5000]
        current_prospects = {p.id for p in await ensure_prospects(session, club, season)}
        assert len(current_prospects) == 3 and not old_prospects & current_prospects
        assert (
            len(
                list(
                    await session.scalars(
                        select(AcademyProspect.id).where(AcademyProspect.id.in_(old_prospects))
                    )
                )
            )
            == 4
        )
        new_ids = set(
            await session.scalars(select(Player.person_id).where(Player.club_id == club_id))
        )
        assert len(new_ids) == 20 and not new_ids & old_players.keys()
        assert (
            set(
                await session.scalars(
                    select(LineupSlot.person_id).where(LineupSlot.club_id == club_id)
                )
            )
            == new_ids
        )
        for pid, status in old_players.items():
            old = await session.get(Player, pid)
            assert old.club_id is None
            if status == "last_season":
                assert old.status == "retired"
            else:
                assert (await session.get(CandidateOffer, pid)).status == "available"
                assert (await session.get(Person, pid)).career == "player"
        assert (await session.get(Staff, old_head)).club_id is None
        head = (await session.scalars(select(Staff).where(Staff.club_id == club_id))).one()
        assert head.role == "head_coach" and head.person_id != old_head
        assert (await session.get(StaffQualification, (head.person_id, head.role))).skill == 1
        contracts = await session.scalars(
            select(SalaryContract.person_id).where(
                SalaryContract.club_id == club_id, SalaryContract.ended_at.is_(None)
            )
        )
        assert set(contracts) == new_ids | {head.person_id}
        adjustment = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.operation_key == f"claim-balance:{claimed.id}")
        )
        assert adjustment.cash_delta == 600_000 - before_cash
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        await credit_locked(session, account, "after-claim", "sponsor", 1000, claim_at)
        assert (await claim_club(session, actor, club_id)).id == claimed.id
        assert account.club.budget == 601_000


async def test_free_club_waits_until_its_locked_match_finishes(sessions):
    _, _, club_id = await setup_world(sessions)
    fixture_id, _ = await start_fixture(sessions, club_id)
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        assert fixture.status == "live"
        owner = await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        at = owner.expires_at
        assert await end_expired(session, owner, at)
        entry = await begin_game(session, telegram_id=1002, update_id=10, payload=None, at=at)
        assert club_id not in {offer.id for offer in entry.offers}
        actor = replace(entry.actor, operation_id=uuid4(), update_id=11)
        with pytest.raises(ClubUnavailable):
            await claim_club(session, actor, club_id)
        assert fixture.status == "live"
        assert await session.scalar(select(ClubClaimReset)) is None
