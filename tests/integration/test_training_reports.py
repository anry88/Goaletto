"""Existing training reports gain detail without changing their immutable receipts."""

from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select

from goaletto.application.training import apply_training, select_training, week_start
from goaletto.application.training_reports import report_details
from goaletto.bot.training_views import training_report
from goaletto.domain.training import Focus, Intensity
from goaletto.infrastructure.models import TrainingPlan
from tests.integration.test_agent_wave import NOW
from tests.integration.test_candidates import action, funded
from tests.integration.test_specialist_training import choose, hired, training_job

pytestmark = pytest.mark.integration


async def test_old_team_and_physical_reports_remove_later_growth_without_writing_history(sessions):
    _, actor, _ = await funded(sessions)
    originals = {}
    expected = {}

    def freeze_legacy(plan):
        expected[plan.id] = {
            row["person_id"]: deepcopy(row["skill_progress"]) for row in plan.results
        }
        plan.results = [
            {key: value for key, value in row.items() if key not in ("position", "skill_progress")}
            for row in plan.results
        ]
        originals[plan.id] = deepcopy(plan.results)

    async with sessions.begin() as session:
        await hired(session, actor, "fitness_coach")
        first = await choose(session, actor, Focus.TEAM)
        owner_id = first.ownership_id
        await apply_training(session, await training_job(session, first), first.due_at)
        freeze_legacy(first)
        for offset in (1, 3):
            plan = await select_training(
                session,
                action(actor),
                ownership_id=owner_id,
                week=week_start(NOW) + timedelta(days=offset),
                focus=Focus.PHYSICAL,
                intensity=Intensity.MEDIUM,
                clock=lambda: NOW,
            )
            await apply_training(session, await training_job(session, plan), plan.due_at)
            freeze_legacy(plan)
    async with sessions.begin() as session:
        for plan_id, original in originals.items():
            plan = await session.get(TrainingPlan, plan_id)
            report = await report_details(session, plan)
            for row in report.results:
                for skill, values in row["skill_progress"].items():
                    frozen = expected[plan_id][row["person_id"]][skill]
                    assert values["gain_bps"] == frozen["gain_bps"]
                    assert values["progress_after"] == frozen["progress_after"]
            body, _ = training_report(report, "Club")
            assert "/100 (+" in body and "Навыки не изменились" not in body
            assert plan.results == original
        await session.flush()
    async with sessions.begin() as session:
        persisted = (await session.scalars(select(TrainingPlan))).all()
        assert all(plan.results == originals[plan.id] for plan in persisted)
