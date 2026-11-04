"""Position evidence is read from durable minutes, including previous clubs."""

from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.club_view import played_positions
from goaletto.infrastructure.models import Fixture, Match
from tests.integration.test_agent_wave import setup_world

pytestmark = pytest.mark.integration


async def test_card_positions_require_positive_saved_minutes(sessions):
    await setup_world(sessions)
    person_id, other_id = uuid4(), uuid4()
    async with sessions.begin() as session:
        fixtures = (await session.scalars(select(Fixture).limit(4))).all()
        for fixture, side, phase, minutes in zip(
            fixtures,
            ("home", "away", "home", "away"),
            ("full_time", "full_time", "first_half", "full_time"),
            ([["MC", 90], ["FC", 0]], [["DR", 60], ["MC", 30]], [["GK", 3]], None),
            strict=True,
        ):
            condition = {"player_id": str(person_id), "minutes_played": 90}
            if minutes is not None:
                condition["position_minutes"] = minutes
            session.add(
                Match(
                    id=uuid4(),
                    fixture_id=fixture.id,
                    seed=7,
                    engine_version="regulation-v7",
                    state_version=7,
                    phase=phase,
                    completed_tick=30 if phase == "full_time" else 1,
                    home_goals=0,
                    away_goals=0,
                    starting_snapshot={side: {"snapshot": {"players": [{"id": str(person_id)}]}}},
                    state={
                        side: {"conditions": [condition]},
                        "away" if side == "home" else "home": {
                            "conditions": [
                                {
                                    "player_id": str(other_id),
                                    "position_minutes": [["FC", 90]],
                                }
                            ]
                        },
                    },
                )
            )
        await session.flush()
        # A committed live tick also proves play; an unused bench slot does not.
        assert await played_positions(session, person_id) == ("DR", "GK", "MC")
        assert await played_positions(session, uuid4()) == ()


@pytest.mark.parametrize("staff_policy", ["staff-session-v2", "staff-session-v3"])
async def test_training_uses_pinned_personal_morale_policy(sessions, monkeypatch, staff_policy):
    import goaletto.application.training as runtime
    from goaletto.application.training_staff import training_staff
    from goaletto.domain.training import Focus
    from goaletto.infrastructure.models import Player
    from tests.integration.test_specialist_training import choose, training_job

    _, actor, club = await setup_world(sessions)
    monkeypatch.setattr(
        runtime,
        "training_staff",
        lambda r, f, **kw: training_staff(r, f, policy=staff_policy),
    )
    monkeypatch.setattr("goaletto.domain.staff_effects.seeded_draw", lambda *a, **kw: 0)
    async with sessions.begin() as session:
        for player in await session.scalars(select(Player).where(Player.club_id == club)):
            player.morale = 0
        plan = await choose(session, actor, Focus.TEAM)
        assert plan.staff_snapshot["policy"] == staff_policy
        job = await training_job(session, plan)
        await runtime.apply_training(session, job, plan.due_at)
        assert all(row["morale_before"] == 0 for row in plan.results)
        expected = 1 if staff_policy == "staff-session-v2" else 0
        assert all(row["morale_after"] == expected for row in plan.results)
        assert all(row["leadership_gain"] > 0 for row in plan.results)
        saved = list(plan.results)
        await runtime.apply_training(session, job, plan.due_at)
        assert plan.results == saved
