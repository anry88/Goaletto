"""Real hire/training/match flows for new specialists and positional experience."""

from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from goaletto.application.history import get_match_report
from goaletto.application.results import finalize_result
from goaletto.application.training import apply_training, load_training
from goaletto.application.training_staff import LEGACY_POLICY, training_staff
from goaletto.bot.history_cards import report_view
from goaletto.domain.training import Focus, available_focus
from goaletto.infrastructure.models import (
    LineupSlot,
    MatchDevelopment,
    Player,
    SalaryContract,
    StaffQualification,
)
from tests.integration.test_agent_wave import start_fixture
from tests.integration.test_candidates import action, funded
from tests.integration.test_match_practice_runtime import completed_checkpoint
from tests.integration.test_specialist_training import choose, hired, training_job

pytestmark = pytest.mark.integration


async def test_new_specialists_hire_and_unlock_profiles_with_real_contracts(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        await hired(session, actor, "attack_coach")
        screen = await load_training(session, action(actor), record=False)
        assert Focus.PLAYMAKING not in screen.available and Focus.WINGER not in screen.available
        midfield = await hired(session, actor, "midfield_coach")
        wing = await hired(session, actor, "wing_coach")
        screen = await load_training(session, action(actor), record=False)
        assert {Focus.PLAYMAKING, Focus.WINGER, Focus.ATTACKING, Focus.SET_PIECES} <= set(
            screen.available
        )
        for person_id, role in ((midfield, "midfield_coach"), (wing, "wing_coach")):
            assert await session.get(StaffQualification, (person_id, role)) is not None
            salary = await session.scalar(
                select(SalaryContract).where(
                    SalaryContract.person_id == person_id, SalaryContract.ended_at.is_(None)
                )
            )
            assert salary.club_id == club and salary.role == role and salary.weekly_rate > 0
        plan = await choose(session, actor, Focus.PLAYMAKING)
        assert plan.staff_snapshot["policy"] == "staff-session-v2"
        assert plan.staff_snapshot["roles"]["midfield_coach"]["person_id"] == str(midfield)
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "applied" and any(row["gain_bps"] > 0 for row in plan.results)
        for row in plan.results:
            from uuid import UUID

            player = await session.get(Player, UUID(row["person_id"]))
            assert player.form_activity_at == plan.due_at and player.form_idle_steps == 0
            assert sum(player.position_experience.values()) > 0


async def test_pending_old_attack_coach_playmaking_session_keeps_frozen_terms(
    sessions, monkeypatch
):
    import goaletto.application.training as runtime

    _, actor, _ = await funded(sessions)
    async with sessions.begin() as session:
        await hired(session, actor, "attack_coach")
        with monkeypatch.context() as old:
            old.setattr(runtime, "available_focus", lambda f, q: available_focus(f, q, legacy=True))
            old.setattr(
                runtime,
                "training_staff",
                lambda r, f, **kw: training_staff(r, f, policy=LEGACY_POLICY),
            )
            plan = await choose(session, actor, Focus.PLAYMAKING)
        assert plan.staff_snapshot["policy"] == LEGACY_POLICY
        await apply_training(session, await training_job(session, plan), plan.due_at)
        assert plan.status == "applied" and any(row["gain_bps"] > 0 for row in plan.results)
        tomorrow = replace(action(actor), at=actor.at + timedelta(days=1))
        with pytest.raises(ValueError, match="specialist"):
            await choose(session, tomorrow, Focus.PLAYMAKING)
        await hired(session, tomorrow, "midfield_coach")
        assert (await choose(session, tomorrow, Focus.PLAYMAKING)).staff_snapshot[
            "policy"
        ] == "staff-session-v2"


async def test_match_rewards_actual_position_and_report_excludes_opponent(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        coach = await hired(session, actor, "midfield_coach")
        slots = (
            await session.scalars(
                select(LineupSlot)
                .where(LineupSlot.club_id == club, LineupSlot.slot <= 11)
                .order_by(LineupSlot.slot)
            )
        ).all()
        defender = next(s for s in slots if s.position == "DC")
        midfielder = next(s for s in slots if s.position == "MC")
        defender.position, midfielder.position = "MC", "DC"
        person_id = defender.person_id
        before = dict((await session.get(Player, person_id)).position_experience)
    fixture, at = await start_fixture(sessions, club)
    final = await completed_checkpoint(sessions, fixture, at)
    async with sessions.begin() as session:
        assert final.engine_version == "regulation-v5"
        await finalize_result(session, fixture, at + timedelta(hours=1))
        player = await session.get(Player, person_id)
        receipt = await session.get(MatchDevelopment, final.match_id)
        row = next(r for r in receipt.results if r["person_id"] == str(person_id))
        assert player.position == "DC"
        assert row["position_terms"][0]["position"] == "MC"
        assert row["position_terms"][0]["role"] == "midfield_coach"
        assert receipt.staff_snapshot[str(club)]["midfield_coach"]["person_id"] == str(coach)
        assert row["granted"]["playmaking"] > 0 and row["granted"]["defending"] == 0
        assert player.position_experience.get("DC", 0) == before.get("DC", 0)
        assert player.position_experience["MC"] > before.get("MC", 0)
        report = await get_match_report(session, action(actor), fixture)
        own = "home" if report.home_club_id == club else "away"
        other = "away" if own == "home" else "home"
        foreign = report.away_lineup if own == "home" else report.home_lineup
        assert all(m.growth_bps is None and not m.position_experience for m in foreign)
        text, keys = report_view(report)
        assert any(b.text == "📈 Прогресс игроков" for row in keys.inline_keyboard for b in row)
        assert not any(
            other in (b.callback_data or "") for row in keys.inline_keyboard for b in row
        )
        text = "\n".join(report_view(report, section=own, page=page)[0] for page in range(1, 5))
        assert "Опыт MC:" in text and "/100 (+" in text and "Форма до матча" not in text
        assert "staff_skill" not in text and "потенциал" not in text and "множитель" not in text
        with pytest.raises(ValueError, match="own"):
            report_view(report, section=other)
        saved = dict(player.position_experience)
        assert not await finalize_result(session, fixture, at + timedelta(hours=2))
        assert player.position_experience == saved


async def test_positional_experience_rejects_negative_and_unknown_positions(sessions):
    from sqlalchemy import text

    _, _, club = await funded(sessions)
    async with sessions.begin() as session:
        person_id = await session.scalar(select(Player.person_id).where(Player.club_id == club))
    for values in ('{"MC":-1}', '{"unknown":1}', '{"MC":1.5}'):
        with pytest.raises(DBAPIError):
            async with sessions.begin() as session:
                await session.execute(
                    text(
                        "UPDATE players SET position_experience=CAST(:values AS jsonb) "
                        "WHERE person_id=:id"
                    ),
                    {"values": values, "id": person_id},
                )
