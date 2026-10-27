import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from goaletto.application.default_club_names import update_default_names
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.world import initialize_world
from goaletto.domain.club_name_catalog import ENGLISH_CLUB_NAMES
from goaletto.infrastructure.models import (
    Club,
    DefaultClubNameReceipt,
    OwnershipPeriod,
    Person,
    WorldSeedReceipt,
)
from tests.integration.test_agent_wave import NOW, OPTIONS, setup_world

pytestmark = pytest.mark.integration
OLD_OPTIONS = replace(OPTIONS, club_naming_policy=None)


async def test_preview_rollback_retry_and_all_historical_owners_are_preserved(sessions):
    _, _, owned = await setup_world(sessions, options=OLD_OPTIONS)
    async with sessions.begin() as session:
        entry = await begin_game(session, telegram_id=1002, update_id=10, payload=None, at=NOW)
        owner = await claim_club(
            session, replace(entry.actor, operation_id=uuid4()), entry.offers[0].id
        )
        former = owner.club_id
        club = await session.get(Club, former)
        club.owner_telegram_id = None
        owner.ended_at, owner.end_reason = NOW, "voluntary"
        # Owner projection without a lease also must never be renamed.
        projection = await session.scalar(
            select(Club).where(Club.id.not_in([owned, former])).order_by(Club.id).limit(1)
        )
        projection.owner_telegram_id = 123456
        excluded = {owned, former, projection.id}
        before = dict((await session.execute(select(Club.id, Club.name))).all())
        money = dict((await session.execute(select(Club.id, Club.budget))).all())
        people = (await session.execute(select(Person.id, Person.name))).all()
        seed_hash = (await session.scalar(select(WorldSeedReceipt))).config_hash
    async with sessions.begin() as session:
        preview = await update_default_names(session, at=NOW)
        assert len(preview) == 47
        assert not excluded.intersection(change.club_id for change in preview)
        assert dict((await session.execute(select(Club.id, Club.name))).all()) == before
        assert await session.scalar(select(func.count()).select_from(DefaultClubNameReceipt)) == 0
    with pytest.raises(RuntimeError, match="rollback"):
        async with sessions.begin() as session:
            assert await update_default_names(session, at=NOW, apply=True) == preview
            raise RuntimeError("rollback")
    async with sessions.begin() as session:
        assert dict((await session.execute(select(Club.id, Club.name))).all()) == before
        assert await update_default_names(session, at=NOW, apply=True) == preview
    async with sessions.begin() as session:
        assert await update_default_names(session, at=NOW + timedelta(days=1), apply=True) == ()
        after = dict((await session.execute(select(Club.id, Club.name))).all())
        assert {key: after[key] for key in excluded} == {key: before[key] for key in excluded}
        assert all(after[c.club_id] == c.after and c.after in ENGLISH_CLUB_NAMES for c in preview)
        assert len(set(after.values())) == 50
        assert dict((await session.execute(select(Club.id, Club.budget))).all()) == money
        assert (await session.execute(select(Person.id, Person.name))).all() == people
        assert (await session.scalar(select(WorldSeedReceipt))).config_hash == seed_hash
        assert await session.scalar(select(func.count()).select_from(DefaultClubNameReceipt)) == 47
        assert await session.scalar(select(func.count()).select_from(OwnershipPeriod)) == 2
        receipt = await session.get(DefaultClubNameReceipt, preview[0].club_id)
        with pytest.raises(DBAPIError, match="immutable"):
            async with session.begin_nested():
                receipt.before_name = "tampered"
                await session.flush()


async def test_concurrent_initializers_avoid_names_across_sets_and_old_retry_is_unchanged(sessions):
    async def seed(key):
        async with sessions.begin() as session:
            await initialize_world(session, replace(OPTIONS, key=key), initialized_at=NOW)

    await asyncio.gather(seed("first"), seed("second"))
    async with sessions.begin() as session:
        names = (await session.scalars(select(Club.name))).all()
        assert len(names) == len(set(names)) == 100
        assert all(name in ENGLISH_CLUB_NAMES for name in names)
        assert await update_default_names(session, at=NOW, apply=True) == ()
    async with sessions.begin() as session:
        old = await initialize_world(session, OLD_OPTIONS, initialized_at=NOW)
        receipt = await session.get(WorldSeedReceipt, OLD_OPTIONS.key)
        assert "club_naming_policy" not in receipt.config
        old_hash = receipt.config_hash
    async with sessions.begin() as session:
        retry = await initialize_world(session, OPTIONS, initialized_at=NOW)
        assert not retry.created and retry.league_set_id == old.league_set_id
        assert (await session.get(WorldSeedReceipt, OLD_OPTIONS.key)).config_hash == old_hash
