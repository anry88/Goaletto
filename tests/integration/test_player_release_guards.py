"""Recheck money, listings, squad/GK minimum and lineup lock at termination."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.market import list_player, withdraw_listing
from goaletto.application.money import InsufficientFunds, locked_accounts, reserve
from goaletto.application.player_release import confirm_release, eligible_player, quote_release
from goaletto.infrastructure.models import Fixture, LedgerEntry, OwnershipPeriod, Player
from tests.integration.test_agent_wave import NOW
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def test_release_reservations_open_auction_and_locked_fixture(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as s:
        oid = await s.scalar(
            select(OwnershipPeriod.id).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        pid = await s.scalar(
            select(Player.person_id)
            .where(Player.club_id == club_id, Player.position != "GK")
            .limit(1)
        )
        q = await quote_release(s, action(actor), oid, pid, clock=lambda: NOW)
        available = (await locked_accounts(s, club_id))[club_id].available
        await reserve(s, club_id, uuid4(), available, {"test": "full-reserve"}, NOW)
        with pytest.raises(InsufficientFunds):
            await confirm_release(s, action(actor), q.id, clock=lambda: NOW)
        assert (await s.get(Player, pid)).club_id == club_id
        auction = await list_player(s, action(actor), oid, pid, 100000, clock=lambda: NOW)
        with pytest.raises(ValueError, match="auction"):
            await confirm_release(s, action(actor), q.id, clock=lambda: NOW)
        await withdraw_listing(s, action(actor), oid, auction.id)
        fixture = await s.scalar(
            select(Fixture)
            .where((Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id))
            .order_by(Fixture.lineup_lock_at)
            .limit(1)
        )
        with pytest.raises(ValueError, match="locked match"):
            await eligible_player(s, club_id, pid, fixture.lineup_lock_at)
        assert not await s.scalar(
            select(LedgerEntry.id).where(LedgerEntry.kind == "player_release")
        )


async def test_release_keeps_goalkeeper_and_counts_outgoing_auctions(sessions):
    _, actor, club_id = await funded(sessions)
    async with sessions.begin() as s:
        oid = await s.scalar(
            select(OwnershipPeriod.id).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        keepers = list(
            (
                await s.scalars(
                    select(Player.person_id).where(
                        Player.club_id == club_id, Player.position == "GK"
                    )
                )
            ).all()
        )
        q = await quote_release(s, action(actor), oid, keepers[0], clock=lambda: NOW)
        await confirm_release(s, action(actor), q.id, clock=lambda: NOW)
        with pytest.raises(ValueError, match="goalkeeper"):
            await quote_release(s, action(actor), oid, keepers[1], clock=lambda: NOW)
        others = list(
            (
                await s.scalars(
                    select(Player.person_id).where(
                        Player.club_id == club_id, Player.position != "GK"
                    )
                )
            ).all()
        )
        for pid in others[:3]:
            await list_player(s, action(actor), oid, pid, 100000, clock=lambda: NOW)
        with pytest.raises(ValueError, match="at least 16"):
            await quote_release(s, action(actor), oid, others[4], clock=lambda: NOW)
