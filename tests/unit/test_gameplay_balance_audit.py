"""Smoke checks for the offline gameplay-balance audit (AND-71, read-only).

Verifies reproducibility, hand-checked budget arithmetic, full mission plus
per-signing accounting, input immutability and growth limits. No test asserts
that the balance itself is 'good': thresholds on means or rates would freeze
an arbitrary snapshot instead of checking correctness.
"""

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from uuid import NAMESPACE_DNS, uuid5

import pytest

AUDIT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_gameplay_balance.py"


def load_audit_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("audit_gameplay_balance", AUDIT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_gameplay_balance"] = module
    spec.loader.exec_module(module)
    return module


AUDIT = load_audit_module()


def test_audit_is_reproducible() -> None:
    first = AUDIT.run_audit([7])
    second = AUDIT.run_audit([7])
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert AUDIT.content_hash(first) == AUDIT.content_hash(second)
    other = AUDIT.run_audit([8])
    assert AUDIT.content_hash(other) != AUDIT.content_hash(first)


def test_budget_arithmetic_on_hand_checked_cycle() -> None:
    from goaletto.domain import finances as F
    from goaletto.domain.sponsorship import sponsor_terms

    content = AUDIT.run_audit([7])
    calm = next(s for s in content["budget"]["scenarios"] if s["key"] == "calm")
    # Hand-checked domain arithmetic: opening sponsor is the tenth-place base.
    assert sponsor_terms(5, None, 2500).cycle_rate == 48_000 + 0 + 500 + 2500
    assert F.merchandise_income(2500) == 2500
    assert F.ticket_income(2500, 5000) == 12_500
    assert F.season_prize(5, 10) == 20_000
    assert calm["sponsor_opening_rate"] == 51_000
    assert calm["staff_per_cycle"] == F.staff_wage("head_coach") == 8_000
    assert calm["upkeep_per_cycle"] == F.stadium_upkeep(5000) == 3_000
    # Cycle 1 of the first club: expenses before income, prize only at cycle 18.
    first = calm["cash_flow_first_club"][0]
    wages = first["expenses"] - calm["staff_per_cycle"] - calm["upkeep_per_cycle"]
    assert calm["wages_min"] <= wages <= calm["wages_max"]
    expected_income = 51_000 + 2_500 + (12_500 if 1 in _home_cycles() else 0)
    assert first["income"] == expected_income
    assert first["cash"] == 600_000 - first["expenses"] + first["income"]
    cycle18 = calm["cash_flow_first_club"][17]
    assert cycle18["income"] >= 51_000 + 2_500 + 20_000  # Prize lands here, never earlier.
    assert all(
        row["income"] < 51_000 + 2_500 + 12_500 + 20_000
        for row in calm["cash_flow_first_club"][:17]
    )


def _home_cycles() -> set[int]:
    from datetime import UTC, datetime

    from goaletto.domain.calendar import league_calendar
    from goaletto.domain.world import WorldOptions, build_world

    world = build_world(
        WorldOptions(
            key="gameplay-balance-audit",
            seed=0,
            first_kickoff=datetime(2026, 10, 19, 18, tzinfo=UTC),
        )
    )
    league = next(league for league in world.leagues if league.tier == 5)
    fixtures = league_calendar(
        season_id=world.season_id,
        league_id=league.id,
        clubs=tuple(club.id for club in league.clubs),
        first_kickoff=datetime(2026, 10, 19, 18, tzinfo=UTC),
        seed=0,
    )
    club = league.clubs[0]
    return {f.round_number for f in fixtures if f.home_club_id == club.id}


def test_mission_cost_counts_search_once_and_each_signing() -> None:
    # One of four signed: the mission is paid in full, not divided by four.
    assert AUDIT.mission_full_cost(500_000, [100_000, 275_000, 539_000, 891_000], [1]) == 775_000
    # Two signed: each adds its own separate fee on top of the same mission.
    assert AUDIT.mission_full_cost(500_000, [100_000, 275_000, 539_000, 891_000], [0, 3]) == (
        1_491_000
    )
    assert AUDIT.mission_full_cost(125_000, [200_000], []) == 125_000


def test_scout_examples_follow_full_mission_accounting() -> None:
    content = AUDIT.run_audit([7])
    examples = content["scout"]["examples_skill5_mc"]
    by_days = {row["days"]: row for row in examples}
    assert set(by_days) == {4, 8, 12, 16}
    for days, row in by_days.items():
        assert len(row["candidates"]) == days // 4
        for candidate in row["candidates"]:
            full = row["mission_fee"] + candidate["signing_fee"]
            assert full >= row["mission_fee"] + 100_000  # Floor, not a band price.
            assert candidate["wage_per_cycle"] > 0


def test_inputs_are_not_mutated() -> None:
    from goaletto.domain import finances as F
    from goaletto.domain.development import GrowthBudget, grant_growth
    from goaletto.domain.scouting import discovery

    person_id = uuid5(NAMESPACE_DNS, "and71-audit:immutability")
    first = discovery(person_id, "MC", 5)
    second = discovery(person_id, "MC", 5)
    assert first == second

    budget = GrowthBudget(21)
    snapshot = dict(budget.used)
    skills = {
        key: 10
        for key in (
            "goalkeeping",
            "defending",
            "playmaking",
            "winger",
            "attacking",
            "set_pieces",
            "stamina",
            "pace",
        )
    }
    caps = dict(skills)
    caps["playmaking"] = 20
    progress = {**{key: 0 for key in skills}, "experience": 0}
    skills_before = dict(skills)
    grant_growth(skills, caps, progress, budget, {"playmaking": 500}, source="training")
    assert budget.used == snapshot
    assert skills == skills_before
    with pytest.raises(dataclasses.FrozenInstanceError):
        budget.age = 22  # type: ignore[misc]
    assert F.player_fee((10,) * 8, 26, "MC") == 300_000
    assert F.scout_mission_fee(5, 4) == 125_000


def test_growth_limits_hold_and_missing_match_coverage_is_reported() -> None:
    from goaletto.domain.development import (
        GROWTH_POLICY,
        GrowthBudget,
        grant_growth,
    )

    assert GROWTH_POLICY == "development-budget-v1"
    budget = GrowthBudget(18)
    skills = {
        key: 10
        for key in (
            "goalkeeping",
            "defending",
            "playmaking",
            "winger",
            "attacking",
            "set_pieces",
            "stamina",
            "pace",
        )
    }
    caps = {key: 20 for key in skills}
    progress = {**{key: 0 for key in skills}, "experience": 0}
    growth = grant_growth(skills, caps, progress, budget, {"playmaking": 99_999}, source="match")
    assert growth.budget.used["match"] <= 10_000
    assert growth.budget.used["playmaking"] <= 15_000
    growth2 = grant_growth(
        growth.skills,
        caps,
        growth.progress,
        growth.budget,
        {"stamina": 99_999, "pace": 99_999},
        source="training",
    )
    used = growth2.budget.used
    assert used["training"] <= 20_000
    assert sum(used[k] for k in skills) <= 30_000

    content = AUDIT.run_audit([7])
    coverage = content["development"]["coverage"]
    assert coverage["status"] == "implementation_gap_not_balance_rule"
    assert set(coverage["gaps"]) == {
        "champions_growth",
        "friendly_growth",
    }
    assert coverage["friendly_factor"] is None
    runs = {(r["age"], r["staff"], r["calendar"]): r for r in content["development"]["runs"]}
    for age in (18, 21, 26, 30, 34):
        for staff in (2, 10, 18):
            free = runs[(age, staff, "free")]
            busy = runs[(age, staff, "busy")]
            # Characterization of current implementation only, not accepted gameplay.
            assert free["league_matches"] == busy["league_matches"] == 18
            assert free["match_bps"] == busy["match_bps"]
            assert free["training_days"] > busy["training_days"]
            assert free["total_bps"] >= busy["total_bps"]
            assert free["training_bps"] <= 20_000
            assert free["match_bps"] <= 10_000
            assert free["total_bps"] <= 30_000


def test_cli_bounds_reject_bad_volume() -> None:
    with pytest.raises(ValueError):
        AUDIT.resolve_seeds(AUDIT.parse_args(["--count", "0"]))
    with pytest.raises(ValueError):
        AUDIT.resolve_seeds(AUDIT.parse_args(["--seeds", "7,7"]))
    with pytest.raises(ValueError):
        AUDIT.resolve_seeds(
            AUDIT.parse_args(["--seed", "7", "--count", "2", "--max-findings", "1"])
        )
    assert AUDIT.resolve_seeds(AUDIT.parse_args(["--seeds", "7,8"])) == [7, 8]


def test_affordability_includes_the_scouts_hire_and_wages():
    from goaletto.domain import finances as F

    result = AUDIT.run_audit([7])["scout"]["affordability"]
    assert result["scout_hire"] == F.staff_fee("scout", 5, 50)
    assert result["scout_wage_per_cycle"] == F.staff_wage("scout", 5, 50)
    assert 10000 <= result["existing_wages_per_cycle"] <= 13000
