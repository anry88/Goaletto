from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.club_view import get_player_card
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.domain.nationalities import nationality
from goaletto.infrastructure.models import DataProtection, Person, Player
from tests.integration.test_agent_wave import NOW, setup_world

pytestmark = pytest.mark.integration


async def test_every_person_is_stable_and_nationality_cannot_be_changed(sessions):
    _, actor, club = await setup_world(sessions)
    async with sessions.begin() as session:
        people = (await session.scalars(select(Person))).all()
        assert len(people) >= 1000
        assert all(p.country_code == nationality(p.id) for p in people)
        player = await session.scalar(select(Player).where(Player.club_id == club))
        card = await get_player_card(
            session, replace(actor, operation_id=uuid4()), player.person_id
        )
        assert card.country_code == nationality(player.person_id)
        code = card.country_code
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE persons SET country_code=:country WHERE id=:person"),
                {"country": "BR" if code != "BR" else "RS", "person": player.person_id},
            )
    async with sessions.begin() as session:
        assert (await session.get(Person, player.person_id)).country_code == code


async def test_internal_claim_does_not_protect_external_claim_latches_and_blocks_reset(sessions):
    await setup_world(sessions)
    async with sessions.begin() as session:
        session.info["production"] = True
        entry = await begin_game(
            session, telegram_id=1002, update_id=1002, payload=None, at=NOW, internal=True
        )
        await claim_club(session, replace(entry.actor, operation_id=uuid4()), entry.offers[0].id)
    async with sessions.begin() as session:
        assert await session.scalar(select(DataProtection.protected_at)) is None
    with pytest.raises(RuntimeError, match="rollback"):
        async with sessions.begin() as session:
            session.info["production"] = True
            entry = await begin_game(
                session, telegram_id=1003, update_id=1003, payload=None, at=NOW
            )
            await claim_club(
                session, replace(entry.actor, operation_id=uuid4()), entry.offers[0].id
            )
            assert await session.scalar(select(DataProtection.protected_at)) == NOW
            for query in (
                "TRUNCATE data_protection",
                "UPDATE data_protection SET protected_at=NULL",
                "DELETE FROM data_protection",
            ):
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.execute(text(query))
            from goaletto.application.data_protection import protect

            await protect(session, NOW + timedelta(days=1))
            assert await session.scalar(select(DataProtection.protected_at)) == NOW
            raise RuntimeError("rollback")
    async with sessions.begin() as session:
        assert await session.scalar(select(DataProtection.protected_at)) is None
        assert await session.scalar(select(func.count()).select_from(Person)) >= 1000
