from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.candidates import load_candidates, sign_candidate
from goaletto.application.history import get_match_report
from goaletto.application.results import finalize_result
from goaletto.bot.history_cards import report_view
from goaletto.infrastructure.models import (
    MatchDevelopment,
    Person,
    Player,
    PlayerDevelopment,
)
from tests.integration.test_agent_wave import NOW, start_fixture
from tests.integration.test_candidates import action, funded
from tests.integration.test_match_practice_runtime import completed_checkpoint

pytestmark = pytest.mark.integration


async def test_all_unused_positions_decay_including_unselected_reserves_and_retry_is_stable(
    sessions,
):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as s:
        screen = await load_candidates(s, action(actor), "player")
        item = min(screen.items, key=lambda item: item.offer.fee)
        await sign_candidate(
            s, action(actor), screen.ownership_id, item.person.id, item.offer.fee, at=NOW
        )
        roster = (await s.scalars(select(Player).where(Player.club_id == club))).all()
        for p in roster:
            p.position_experience = {"GK": 500000, "FC": 500000, "MC": 500000, "DC": 500000}
        before = {p.person_id: dict(p.position_experience) for p in roster}
    fixture, at = await start_fixture(sessions, club)
    final = await completed_checkpoint(sessions, fixture, at)
    async with sessions.begin() as s:
        receipt = await s.get(MatchDevelopment, final.match_id)
        assert receipt.policy_version == "match-practice-v5"
        assert (
            len(
                [
                    v
                    for v in receipt.terms_snapshot["experience_roster"].values()
                    if v["club_id"] == str(club)
                ]
            )
            == 21
        )
        await finalize_result(s, fixture, at + timedelta(hours=1))
        own = {r["person_id"]: r for r in receipt.results if r["club_id"] == str(club)}
        assert len(own) == 21
        for p in (await s.scalars(select(Player).where(Player.club_id == club))).all():
            row = own[str(p.person_id)]
            played = {term["position"] for term in row.get("position_terms", [])}
            for position, value in before[p.person_id].items():
                if position not in played:
                    assert p.position_experience[position] < value
                    assert row["position_experience"][position]["loss"] > 0
            assert all(0 <= value <= 1000000 for value in p.position_experience.values())
        frozen = deepcopy(receipt.results)
        stored = {
            p.person_id: dict(p.position_experience)
            for p in (await s.scalars(select(Player).where(Player.club_id == club))).all()
        }
        report = await get_match_report(s, action(actor), fixture)
        side = "home" if report.home_club_id == club else "away"
        members = report.home_lineup if side == "home" else report.away_lineup
        assert len(members) == 21
        body = "\n".join(report_view(report, section=side, page=page)[0] for page in range(1, 6))
        assert "Опыт " in body and "% (-" in body
        assert not await finalize_result(s, fixture, at + timedelta(hours=2))
        assert receipt.results == frozen
        assert stored == {
            p.person_id: dict(p.position_experience)
            for p in (await s.scalars(select(Player).where(Player.club_id == club))).all()
        }
        for sql in (
            "UPDATE players SET position_experience=CAST(:values AS jsonb) WHERE person_id=:id",
        ):
            with pytest.raises(DBAPIError):
                async with s.begin_nested():
                    await s.execute(
                        text(sql), {"id": next(iter(stored)), "values": '{"MC":1000001}'}
                    )


async def test_zero_initial_experience_and_ten_percent_foreign_training(
    sessions,
):
    from goaletto.application.training import apply_training
    from goaletto.domain.training import Focus
    from goaletto.infrastructure.models import LineupSlot
    from tests.integration.test_specialist_training import choose, training_job

    _, actor, club = await funded(sessions)
    async with sessions.begin() as s:
        players = (await s.scalars(select(Player).where(Player.club_id == club))).all()
        assert all(
            p.position_experience == {} and p.position_experience_policy == "position-experience-v2"
            for p in players
        )
        slot = await s.scalar(
            select(LineupSlot).where(LineupSlot.club_id == club, LineupSlot.slot == 2)
        )
        p = await s.get(Player, slot.person_id)
        p.position = "DC"
        p.alternative_positions = []
        slot.position = "MC"
        (await s.get(Person, p.person_id)).age = 32
        dev = await s.get(PlayerDevelopment, p.person_id)
        dev.growth_age = 32
        from goaletto.domain.development import BUDGET_KEYS

        dev.season_gains = dict.fromkeys(BUDGET_KEYS, 0)
        plan = await choose(s, actor, Focus.TEAM)
        await apply_training(s, await training_job(s, plan), plan.due_at)
        row = next(r for r in plan.results if r["person_id"] == str(p.person_id))
        assert row["legacy_experience_gain"] == 3
        assert row["experience_gain"] == 30 and p.position_experience == {"MC": 30}
        assert row["experience_policy"] == "position-experience-v2"


async def test_frozen_v5_keeps_full_foreign_gain_without_new_inactivity_decay(
    sessions, monkeypatch
):
    import goaletto.application.match_runtime as runtime
    from goaletto.domain.match_codec import state_hash
    from goaletto.domain.match_types import EnginePolicy
    from goaletto.domain.training import age_multiplier

    original = runtime.start_match

    def old_start(*args, **kwargs):
        kwargs.update(
            engine_version="regulation-v5", policy=EnginePolicy(version="match-policy-v5")
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "start_match", old_start)
    _, _, club = await funded(sessions)
    async with sessions.begin() as s:
        for p in (await s.scalars(select(Player).where(Player.club_id == club))).all():
            p.position_experience = {"GK": 400000, "FC": 400000, "MC": 400000, "DC": 400000}
        before = {
            p.person_id: dict(p.position_experience)
            for p in (await s.scalars(select(Player).where(Player.club_id == club))).all()
        }
    fixture, at = await start_fixture(sessions, club)
    final = await completed_checkpoint(sessions, fixture, at)
    frozen_hash = state_hash(final)
    async with sessions.begin() as s:
        receipt = await s.get(MatchDevelopment, final.match_id)
        assert receipt.policy_version == "match-practice-v4"
        assert "experience_roster" not in receipt.terms_snapshot
        await finalize_result(s, fixture, at + timedelta(hours=1))
        own = {r["person_id"]: r for r in receipt.results if r["club_id"] == str(club)}
        for p in (await s.scalars(select(Player).where(Player.club_id == club))).all():
            row = own.get(str(p.person_id))
            gains = (
                {
                    t["position"]: 1000 * age_multiplier(row["age"]) * t["minutes"] // (10000 * 90)
                    for t in row["position_terms"]
                }
                if row
                else {}
            )
            for position in before[p.person_id].keys() | gains.keys():
                assert p.position_experience.get(position, 0) == min(
                    1000000, before[p.person_id].get(position, 0) + gains.get(position, 0) * 100
                )
        assert state_hash(final) == frozen_hash
