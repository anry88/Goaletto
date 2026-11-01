from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.training import apply_training, load_training, select_training
from goaletto.application.training_reports import report_details
from goaletto.domain.development import BUDGET_KEYS
from goaletto.domain.training import (
    LEGACY_SESSION_POLICY,
    POSITIONS,
    SESSION_POLICY,
    Focus,
    Intensity,
)
from goaletto.infrastructure.models import (
    LineupSlot,
    Person,
    Player,
    PlayerDevelopment,
    PlayerRecovery,
    StaffQualification,
)
from tests.integration.test_agent_wave import NOW
from tests.integration.test_candidates import action, funded
from tests.integration.test_specialist_training import choose, hired, training_job

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("legacy", [False, True])
async def test_whole_squad_participates_with_exact_age_adjusted_grants_and_frozen_v2(
    sessions, monkeypatch, legacy
):
    import goaletto.application.training as runtime

    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        coach = await hired(session, actor, "defence_coach")
        (await session.get(StaffQualification, (coach, "defence_coach"))).skill = 10
        players = (
            await session.scalars(
                select(Player).where(Player.club_id == club).order_by(Player.person_id)
            )
        ).all()
        positions = {
            slot.person_id: slot.position
            for slot in (
                await session.scalars(
                    select(LineupSlot).where(LineupSlot.club_id == club, LineupSlot.slot <= 11)
                )
            ).all()
        }
        for p in players:
            (await session.get(Person, p.person_id)).age = 22
            p.defending = 10
            dev = await session.get(PlayerDevelopment, p.person_id)
            dev.growth_age = 22
            dev.season_gains = dict.fromkeys(BUDGET_KEYS, 0)
            dev.caps = {**dev.caps, "defending": 15}
        players[0].injured_until = NOW + timedelta(days=10)
        players[1].injured_until = NOW + timedelta(days=10)
        with monkeypatch.context() as old:
            if legacy:
                old.setattr(runtime, "SESSION_POLICY", LEGACY_SESSION_POLICY)
            plan = await choose(session, actor, Focus.DEFENDING)
        assert plan.policy_version == (LEGACY_SESSION_POLICY if legacy else SESSION_POLICY)
        job = await training_job(session, plan)
        await apply_training(session, job, plan.due_at)
        rows = {r["person_id"]: r for r in plan.results}
        for p in players:
            profile = positions.get(p.person_id, p.position) in POSITIONS[Focus.DEFENDING]
            attended = p not in players[:2] and (profile or not legacy)
            assert (str(p.person_id) in rows) == attended
            dev = await session.get(PlayerDevelopment, p.person_id)
            expected = (216 if profile else 21) if attended else 0
            assert dev.progress["defending"] == expected
            if attended:
                row = rows[str(p.person_id)]
                assert row["gain_bps"] == expected
                assert row["skill_progress"]["defending"]["progress_after"] == expected
                assert row["experience_gain"] > 0 and row["leadership_gain"] > 0
                assert (await session.get(PlayerRecovery, p.person_id)).energy_bps == 9860
        assert len(rows) == (
            len(players) - 2
            if not legacy
            else sum(
                positions.get(p.person_id, p.position) in POSITIONS[Focus.DEFENDING]
                for p in players[2:]
            )
        )
        frozen = deepcopy(plan.results)
        energy = {
            p.person_id: p.energy_bps for p in (await session.scalars(select(PlayerRecovery))).all()
        }
        assert (await report_details(session, plan)).results == frozen
        await apply_training(session, job, plan.due_at + timedelta(minutes=1))
        assert plan.results == frozen
        assert energy == {
            p.person_id: p.energy_bps for p in (await session.scalars(select(PlayerRecovery))).all()
        }


async def test_explicit_reselection_creates_v3_pin_without_changing_old_identity_or_retry(
    sessions, monkeypatch
):
    import goaletto.application.training as runtime

    _, actor, _ = await funded(sessions)
    async with sessions.begin() as session:
        await hired(session, actor, "defence_coach")
        screen = await load_training(session, action(actor), record=False)
        request = action(actor)
        kwargs = dict(
            ownership_id=screen.ownership_id,
            week=screen.week_start,
            focus=Focus.DEFENDING,
            intensity=Intensity.MEDIUM,
        )
        with monkeypatch.context() as old_policy:
            old_policy.setattr(runtime, "SESSION_POLICY", LEGACY_SESSION_POLICY)
            old = await select_training(session, request, **kwargs, clock=lambda: NOW)
        snapshot = deepcopy(old.staff_snapshot)
        old_job = await training_job(session, old)
        with pytest.raises(DBAPIError, match="identity"):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE training_plans SET policy_version=:policy WHERE id=:id"),
                    {"id": old.id, "policy": SESSION_POLICY},
                )
        newer = replace(request, operation_id=uuid4(), at=NOW + timedelta(seconds=1))
        current = await select_training(session, newer, **kwargs, clock=lambda: newer.at)
        assert current.id != old.id and current.policy_version == SESSION_POLICY
        assert old.status == "cancelled" and old.results == [{"reason": "reselected"}]
        assert old.policy_version == LEGACY_SESSION_POLICY and old.staff_snapshot == snapshot
        assert await select_training(session, request, **kwargs, clock=lambda: NOW) is old
        await apply_training(session, old_job, old.due_at)
        assert current.status == "scheduled"
        await apply_training(session, await training_job(session, current), current.due_at)
        assert len(current.results) == 20
