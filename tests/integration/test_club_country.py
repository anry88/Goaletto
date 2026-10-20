"""Club country changes only future academy offers; identities and money stay put."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.academy import load_academy, sign_candidate
from goaletto.application.club_country import choose, screen
from goaletto.application.game_actions import PermissionDenied
from goaletto.domain.academy import candidate
from goaletto.domain.person_names import person_name
from goaletto.infrastructure.models import AcademyCandidate, Club, Person, Player
from tests.integration.test_candidates import action, funded

pytestmark = pytest.mark.integration


async def current_people(session, club):
    return (
        await session.execute(
            select(
                Person.id,
                Person.name,
                Person.country_code,
                Person.appearance_identity_id,
                Player.playmaking,
                Player.defending,
            )
            .join(Player)
            .where(Player.club_id == club)
            .order_by(Person.id)
        )
    ).all()


async def test_country_is_confirmed_once_and_only_new_academy_people_inherit_it(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        state = await screen(session, action(actor))
        assert state.country_code is None
        before = await current_people(session, club)
        budget = (await session.get(Club, club)).budget
        selected = await choose(session, action(actor), state.ownership_id, "UA")
        assert selected.country_code == "UA"
        assert await current_people(session, club) == before
        assert (await session.get(Club, club)).budget == budget
        intake = await load_academy(session, action(actor))
        assert all(c.country_code == "UA" for c in intake.candidates)
        rows = (
            await session.scalars(select(AcademyCandidate).where(AcademyCandidate.club_id == club))
        ).all()
        assert all(c.name == person_name(c.id, "UA") for c in rows)
        key = rows[0].id
        slot = rows[0].slot
    async with sessions.begin() as session:
        assert (await choose(session, action(actor), state.ownership_id, "UA")).country_code == "UA"
        with pytest.raises(ValueError):
            await choose(session, action(actor), state.ownership_id, "RU")
        with pytest.raises(PermissionDenied):
            await choose(session, action(actor), uuid4(), "UA")
        await sign_candidate(session, action(actor), state.ownership_id, slot)
        person = await session.get(Person, key)
        assert person.country_code == "UA" and person.name == person_name(key, "UA")
    async with sessions.begin() as session:
        with pytest.raises(DBAPIError, match="already selected"):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE clubs SET country_code='BY' WHERE id=:key"), {"key": club}
                )
        with pytest.raises(DBAPIError, match="immutable"):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE persons SET country_code='RU' WHERE id=:key"), {"key": key}
                )


async def test_existing_intake_is_not_changed_by_later_country_choice(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        old = await load_academy(session, action(actor))
        before = [(c.name, c.country_code, c.skills) for c in old.candidates]
        await choose(session, action(actor), old.ownership_id, "BY")
        after = await load_academy(session, action(actor))
        assert [(c.name, c.country_code, c.skills) for c in after.candidates] == before
        person = await sign_candidate(
            session, action(actor), old.ownership_id, old.candidates[0].slot
        )
        assert (await session.get(Person, person)).country_code == old.candidates[0].country_code


def test_country_and_name_do_not_change_academy_skill_potential_age_or_position():
    club, season = uuid4(), uuid4()
    a = candidate(club, season, 1, 3, country="UA")
    b = candidate(club, season, 1, 3, country="RU")
    assert a.country_code == "UA" and b.country_code == "RU"
    assert replace(a, name=b.name, country_code=b.country_code) == b
