"""Audited one-time starter migration on the single disposable LOCAL database."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.market import list_player
from goaletto.application.recovery import recover_player
from goaletto.application.starter_rebalance import apply, plan
from goaletto.application.world import initialize_world
from goaletto.domain.starter_squads import LEGACY_STARTER_POLICY
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    Club,
    FinanceAccount,
    LedgerEntry,
    OwnershipPeriod,
    Person,
    Player,
    PlayerDevelopment,
    PlayerRecovery,
    SalaryContract,
    StarterRebalanceReceipt,
)
from tests.integration.test_agent_wave import NOW, OPTIONS, setup_world, start_fixture
from tests.integration.test_candidates import action

pytestmark = pytest.mark.integration
OLD_OPTIONS = replace(OPTIONS, starter_policy=LEGACY_STARTER_POLICY)


async def test_migration_preserves_earned_growth_contracts_history_and_recovery_once(sessions):
    _, actor, club_id = await setup_world(sessions, options=OLD_OPTIONS)
    async with sessions.begin() as session:
        p = await session.scalar(
            select(Player).where(Player.club_id == club_id).order_by(Player.person_id)
        )
        person_id = p.person_id
        p.attacking += 2
        dev = await session.get(PlayerDevelopment, person_id)
        dev.progress = {**dev.progress, "attacking": 4321}
        dev.growth_age = (await session.get(Person, person_id)).age
        dev.season_gains = {"training": 20000, "match": 5000, "attacking": 15000}
        dev_before = (dict(dev.caps), dict(dev.progress), dict(dev.season_gains), dev.growth_age)
        row = await recover_player(session, person_id, NOW)
        row.energy_bps, row.remainder, row.recovered_at = 5432, 1234, NOW
        person = await session.get(Person, person_id)
        identity = (person.name, person.age, person.appearance_identity_id, person.country_code)
        wages = (await session.execute(select(SalaryContract.id, SalaryContract.weekly_rate))).all()
        ledger_count = await session.scalar(select(func.count()).select_from(LedgerEntry))
        target_list, summary = await plan(session)
        target = next(t for t in target_list if t.player.person_id == person_id)
        expected = target.after
        assert summary["eligible"] == 1000 and summary["clubs"] == 50
        assert summary["quality_after_median"] < 7
        fingerprint = summary["fingerprint"]
    async with sessions.begin() as session:
        result = await apply(session, at=NOW, expected_fingerprint=fingerprint)
        assert result["applied"] == 1000
    async with sessions.begin() as session:
        p = await session.get(Player, person_id)
        assert tuple(getattr(p, k) for k in SKILL_NAMES[:8]) == expected
        dev = await session.get(PlayerDevelopment, person_id)
        assert (dev.caps, dev.progress, dev.season_gains, dev.growth_age) == dev_before
        person = await session.get(Person, person_id)
        assert (
            person.name,
            person.age,
            person.appearance_identity_id,
            person.country_code,
        ) == identity
        assert (
            await session.execute(select(SalaryContract.id, SalaryContract.weekly_rate))
        ).all() == wages
        assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == ledger_count
        recovery = await session.get(PlayerRecovery, person_id)
        assert (recovery.energy_bps, recovery.remainder, recovery.stamina) == (
            5432,
            1234,
            p.stamina,
        )
        account = await session.get(FinanceAccount, club_id)
        assert account.credit_limit == sum(account.valuation.values())
        _, summary = await plan(session)
        assert summary["eligible"] == 0 and summary["skipped"]["already_applied"] == 1000
        assert (await apply(session, at=NOW, expected_fingerprint=summary["fingerprint"]))[
            "applied"
        ] == 0
        retry = await initialize_world(session, OLD_OPTIONS, initialized_at=NOW)
        assert not retry.created
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE starter_rebalance_receipts SET applied_at=applied_at")
            )


async def test_changed_preview_and_open_auctions_are_protected(sessions):
    _, actor, club_id = await setup_world(sessions, options=OLD_OPTIONS)
    async with sessions.begin() as session:
        _, initial = await plan(session)
        p = await session.scalar(
            select(Player).where(Player.club_id == club_id, Player.status == "active").limit(1)
        )
        listed_id = p.person_id
        ownership_id = await session.scalar(
            select(OwnershipPeriod.id).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        await list_player(
            session, action(actor), ownership_id, listed_id, 100000, clock=lambda: NOW
        )
    async with sessions.begin() as session:
        targets, preview = await plan(session)
        assert preview["eligible"] == 999 and preview["skipped"]["open_auction"] == 1
        assert listed_id not in {t.player.person_id for t in targets}
    with pytest.raises(ValueError, match="preview changed"):
        async with sessions.begin() as session:
            await apply(session, at=NOW, expected_fingerprint=initial["fingerprint"])
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(StarterRebalanceReceipt)) == 0


async def test_locked_match_and_administrative_insolvency_block_entire_migration(sessions):
    _, _, club_id = await setup_world(sessions, options=OLD_OPTIONS)
    async with sessions.begin() as session:
        club = await session.get(Club, club_id)
        club.budget = -100000000
        _, preview = await plan(session)
        assert preview["clubs_at_risk"] == 1
    with pytest.raises(ValueError, match="insolvency"):
        async with sessions.begin() as session:
            await apply(session, at=NOW, expected_fingerprint=preview["fingerprint"])
    async with sessions.begin() as session:
        (await session.get(Club, club_id)).budget = 50000
    await start_fixture(sessions, club_id)
    async with sessions.begin() as session:
        _, preview = await plan(session)
        assert preview["locked_matches"] > 0
    with pytest.raises(ValueError, match="locked matches"):
        async with sessions.begin() as session:
            await apply(
                session, at=NOW + timedelta(days=7), expected_fingerprint=preview["fingerprint"]
            )
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(StarterRebalanceReceipt)) == 0


async def test_new_world_is_not_migrated_and_form_ten_is_persistable(sessions):
    await setup_world(sessions)
    async with sessions.begin() as session:
        _, preview = await plan(session)
        assert preview["eligible"] == 0 and preview["skipped"]["new_policy"] == 1000
        p = await session.scalar(select(Player).limit(1))
        p.form = 10
        await session.flush()
        await session.refresh(p)
        assert p.form == 10
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(text("UPDATE players SET form=11"))
