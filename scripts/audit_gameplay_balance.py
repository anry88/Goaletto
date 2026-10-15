"""Offline reproducible gameplay-balance audit (AND-71, read-only part).

Calls the current pure domain formulas only; changes no game coefficients,
touches no database, Telegram, network or external AI, and creates no second
match engine or new simulation framework. Match results are NOT simulated
here: league income uses the pinned structural calendar (home/away rounds),
exactly like scripts/calculate_economy.py.

Reproduction::

    python scripts/audit_gameplay_balance.py --seed 7 --count 4 \\
        --commit <sha> --output docs/reports/gameplay-balance-2026-10-15

Writes ``<output>.json`` (compact report data) and ``<output>.md`` (human
summary). Identical arguments produce identical files; wall-clock time is
printed to stdout only and never stored in the reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid5

from goaletto.domain import finances as F
from goaletto.domain import recovery as recovery_mod
from goaletto.domain import scouting as scouting_mod
from goaletto.domain import sponsorship as sponsorship_mod
from goaletto.domain import training as training_mod
from goaletto.domain.calendar import league_calendar
from goaletto.domain.development import (
    GROWTH_POLICY,
    SOURCE_LIMITS,
    GrowthBudget,
    grant_growth,
)
from goaletto.domain.match_development import POLICY as MATCH_DEV_POLICY
from goaletto.domain.match_development import develop_appearance
from goaletto.domain.recovery import daily_recovery, ready_in
from goaletto.domain.scouting import discovery, findings_count
from goaletto.domain.sponsorship import sponsor_terms
from goaletto.domain.training import (
    PROGRESS_KEYS,
    SKILLS,
    Focus,
    Intensity,
    TrainingPlayer,
    train_player,
)
from goaletto.domain.world import WorldOptions, build_world

TOOL_VERSION = "and71-balance-audit-v1"
WORLD_KEY = "gameplay-balance-audit"
WORLD_SEED = 0
FIRST_KICKOFF = datetime(2026, 10, 19, 18, tzinfo=UTC)
TIER = 5
SUPPORTERS = 2500
HOME_CROWD = 2500
BASE_CAPACITY = 5000
SEASON_CYCLES = 18
TOTAL_CYCLES = 20  # 18 season cycles of 4 days + 8 offseason days = 80 days.
CUP_BONUS_CYCLE = 17  # Hypothetical neutral-final split timing, flagged as such.
HYPOTHETICAL_CUP_SPLIT = 12_500

SCOUT_SKILLS = (1, 5, 10, 15, 20)
SCOUT_POSITIONS = ("MC", "FC", "DC")
SCOUT_DAYS = (4, 8, 12, 16)
STRONG_Q = 12  # Analytical band, not a user-approved threshold.
ELITE_Q = 15

DEV_AGES = (18, 21, 26, 30, 34)
DEV_STAFF = (2, 10, 18)
DEV_FOCUS = Focus.PHYSICAL
DEV_INTENSITY = Intensity.MEDIUM

POLICY_VERSIONS = {
    "economy": F.POLICY_VERSION,
    "scouting": scouting_mod.POLICY,
    "training_session": training_mod.SESSION_POLICY,
    "training_schema": training_mod.POLICY_VERSION,
    "development_budget": GROWTH_POLICY,
    "match_development": MATCH_DEV_POLICY,
    "recovery": recovery_mod.POLICY,
    "season_sponsor": sponsorship_mod.POLICY,
}

# Budget scenarios: fixed seasonal sponsor (opening tenth-place base, renewal
# by final place/destination tier), season prize only with cycle 18, expenses
# before income each cycle. Cup income appears only in the flagged row.
BUDGET_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "key": "calm",
        "label": "Спокойный старт",
        "staff": {"head_coach": (1, 50)},
        "extra_staff": {},
        "adult": None,
        "capacity": BASE_CAPACITY,
        "crowd": HOME_CROWD,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": 0,
        "hypothetical": False,
    },
    {
        "key": "specialist",
        "label": "Профильный специалист Q2",
        "staff": {"head_coach": (1, 50)},
        "extra_staff": {"defence_coach": (5, 50)},
        "adult": None,
        "capacity": BASE_CAPACITY,
        "crowd": HOME_CROWD,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": 0,
        "hypothetical": False,
    },
    {
        "key": "scout_idle",
        "label": "Бездействующий скаут Q2",
        "staff": {"head_coach": (1, 50)},
        "extra_staff": {"scout": (5, 50)},
        "adult": None,
        "capacity": BASE_CAPACITY,
        "crowd": HOME_CROWD,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": 0,
        "hypothetical": False,
    },
    {
        "key": "adult_signing",
        "label": "Покупка взрослого Q10/26",
        "staff": {"head_coach": (1, 50)},
        "extra_staff": {},
        "adult": {"skills": (10,) * 8, "age": 26, "position": "MC", "positions": 1},
        "capacity": BASE_CAPACITY,
        "crowd": HOME_CROWD,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": 0,
        "hypothetical": False,
    },
    {
        "key": "expensive_staff",
        "label": "Дорогой штаб (главный Q3 + защита Q2)",
        "staff": {"head_coach": (9, 50)},
        "extra_staff": {"defence_coach": (5, 50)},
        "adult": None,
        "capacity": BASE_CAPACITY,
        "crowd": HOME_CROWD,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": 0,
        "hypothetical": False,
    },
    {
        "key": "stadium",
        "label": "Расширение стадиона до 10K + тренер Q2",
        "staff": {"head_coach": (5, 50)},
        "extra_staff": {},
        "adult": None,
        "capacity": 10_000,
        "crowd": HOME_CROWD,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": 0,
        "hypothetical": False,
        "stadium_price": 300_000,
    },
    {
        "key": "bad_stands",
        "label": "Неблагоприятное место/посещаемость",
        "staff": {"head_coach": (1, 50)},
        "extra_staff": {},
        "adult": None,
        "capacity": BASE_CAPACITY,
        "crowd": 0,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": 0,
        "hypothetical": False,
    },
    {
        "key": "promotion",
        "label": "Повышение в tier 4 (место 2)",
        "staff": {"head_coach": (1, 50)},
        "extra_staff": {},
        "adult": None,
        "capacity": BASE_CAPACITY,
        "crowd": HOME_CROWD,
        "final_place": 2,
        "dest_tier": 4,
        "prize": (5, 2),
        "cup_bonus": 0,
        "hypothetical": False,
    },
    {
        "key": "cup_hypothetical",
        "label": "Гипотетический финал кубка +12.5K",
        "staff": {"head_coach": (1, 50)},
        "extra_staff": {},
        "adult": None,
        "capacity": BASE_CAPACITY,
        "crowd": HOME_CROWD,
        "final_place": 10,
        "dest_tier": 5,
        "prize": (5, 10),
        "cup_bonus": HYPOTHETICAL_CUP_SPLIT,
        "hypothetical": True,
    },
)


def quantiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0, "max": 0.0}
    ordered = sorted(values)
    count = len(ordered)

    def pick(rank: float) -> float:
        position = rank * (count - 1)
        low = int(position)
        high = min(count - 1, low + 1)
        frac = position - low
        return ordered[low] + (ordered[high] - ordered[low]) * frac

    return {
        "min": float(ordered[0]),
        "p25": pick(0.25),
        "median": pick(0.5),
        "p75": pick(0.75),
        "max": float(ordered[-1]),
    }


def mission_full_cost(mission_fee: int, signing_fees: list[int], signed: list[int]) -> int:
    """Full price of a mission outcome: the search is paid once in full.

    Signing one of four candidates does NOT divide the mission fee by four;
    each signed candidate adds its own separate fee.
    """
    return mission_fee + sum(signing_fees[i] for i in signed)


def _scenario_costs(spec: dict[str, Any]) -> tuple[int, int, int]:
    """Return (staff_per_cycle, upfront, extra_player_wage)."""
    staff_cycle = F.staff_wage("head_coach", *spec["staff"]["head_coach"])
    upfront = 0
    for role, (skill, age) in spec["extra_staff"].items():
        staff_cycle += F.staff_wage(role, skill, age)
        upfront += F.staff_fee(role, skill, age)
    if spec["staff"]["head_coach"] != (1, 50):
        skill, age = spec["staff"]["head_coach"]
        upfront += F.staff_fee("head_coach", skill, age)
    upfront += spec.get("stadium_price", 0)
    extra_wage = 0
    adult = spec["adult"]
    if adult is not None:
        upfront += F.player_fee(adult["skills"], adult["age"], adult["position"])
        extra_wage = F.player_wage(
            adult["skills"], adult["age"], adult["position"], adult["positions"]
        )
    return staff_cycle, upfront, extra_wage


def budget_section() -> dict[str, Any]:
    world = build_world(WorldOptions(key=WORLD_KEY, seed=WORLD_SEED, first_kickoff=FIRST_KICKOFF))
    league = next(league for league in world.leagues if league.tier == TIER)
    fixtures = league_calendar(
        season_id=world.season_id,
        league_id=league.id,
        clubs=tuple(club.id for club in league.clubs),
        first_kickoff=FIRST_KICKOFF,
        seed=WORLD_SEED,
    )
    home_cycles = {
        club.id: {f.round_number for f in fixtures if f.home_club_id == club.id}
        for club in league.clubs
    }
    wages = {
        club.id: sum(
            F.player_wage(player.skills[:8], player.age, player.position) for player in club.players
        )
        for club in league.clubs
    }
    opening_rate = sponsor_terms(TIER, None, SUPPORTERS).cycle_rate
    scenarios: list[dict[str, Any]] = []
    for spec in BUDGET_SCENARIOS:
        staff_cycle, upfront, extra_wage = _scenario_costs(spec)
        upkeep = F.stadium_upkeep(spec["capacity"])
        renewal_rate = sponsor_terms(spec["dest_tier"], spec["final_place"], SUPPORTERS).cycle_rate
        prize_tier, prize_place = spec["prize"]
        prize = F.season_prize(prize_tier, prize_place)
        upkeep_total = upkeep * TOTAL_CYCLES
        per_cycle_rows: list[dict[str, int]] = []
        minima: list[int] = []
        endings: list[int] = []
        shortfalls: list[int | None] = []
        for index, club in enumerate(league.clubs):
            cash = F.INITIAL_CAPITAL - upfront
            minimum = cash
            shortfall: int | None = 0 if cash < 0 else None
            for cycle in range(1, TOTAL_CYCLES + 1):
                out = wages[club.id] + staff_cycle + extra_wage + upkeep
                cash -= out
                minimum = min(minimum, cash)
                if cash < 0 and shortfall is None:
                    shortfall = cycle
                rate = opening_rate if cycle <= SEASON_CYCLES else renewal_rate
                income = rate + F.merchandise_income(SUPPORTERS)
                if cycle <= SEASON_CYCLES and cycle in home_cycles[club.id]:
                    income += F.ticket_income(spec["crowd"], spec["capacity"])
                if cycle == SEASON_CYCLES:
                    income += prize
                if spec["cup_bonus"] and cycle == CUP_BONUS_CYCLE:
                    income += spec["cup_bonus"]
                cash += income
                if index == 0:
                    per_cycle_rows.append(
                        {"cycle": cycle, "expenses": out, "income": income, "cash": cash}
                    )
            minima.append(minimum)
            endings.append(cash)
            shortfalls.append(shortfall)
        present = [s for s in shortfalls if s is not None]
        scenarios.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "hypothetical": spec["hypothetical"],
                "wages_min": min(wages.values()),
                "wages_max": max(wages.values()),
                "staff_per_cycle": staff_cycle,
                "upfront": upfront,
                "extra_player_wage": extra_wage,
                "upkeep_per_cycle": upkeep,
                "upkeep_total": upkeep_total,
                "sponsor_opening_rate": opening_rate,
                "sponsor_renewal_rate": renewal_rate,
                "season_prize": prize,
                "min_pre_income": min(minima),
                "ending_min": min(endings),
                "ending_max": max(endings),
                "first_shortfall_cycle": min(present) if present else None,
                "cash_flow_first_club": per_cycle_rows,
            }
        )
    return {
        "world_key": WORLD_KEY,
        "world_seed": WORLD_SEED,
        "tier": TIER,
        "supporters": SUPPORTERS,
        "cycles": TOTAL_CYCLES,
        "season_cycles": SEASON_CYCLES,
        "scenarios": scenarios,
    }


def _discovery_record(
    seed: int, rep: int, skill: int, position: str, days: int, index: int
) -> dict[str, Any]:
    person_id = uuid5(NAMESPACE_DNS, f"and71-audit:{seed}:{rep}:{skill}:{position}:{days}:{index}")
    found = discovery(person_id, position, skill)
    skills8 = found.skills[:8]
    primary = F.PRIMARY[position]
    quality_units = 3 * skills8[primary] + skills8[6] + skills8[7]
    positions = 1 + len(found.alternatives)
    fee = F.scout_signing_fee(skills8, found.age, position, positions)
    wage = F.player_wage(skills8, found.age, position, positions)
    return {
        "person_id": str(person_id),
        "age": found.age,
        "position": position,
        "alternatives": list(found.alternatives),
        "positions": positions,
        "skills8": list(skills8),
        "quality_units": quality_units,
        "quality_floor": quality_units // 5,
        "signing_fee": fee,
        "wage_per_cycle": wage,
        "mission_fee": F.scout_mission_fee(skill, days),
    }


def scout_section(seeds: list[int]) -> dict[str, Any]:
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    examples: list[dict[str, Any]] = []
    for seed in seeds:
        for skill in SCOUT_SKILLS:
            for position in SCOUT_POSITIONS:
                for days in SCOUT_DAYS:
                    mission_fee = F.scout_mission_fee(skill, days)
                    count = findings_count(days)
                    discoveries = [
                        _discovery_record(seed, 0, skill, position, days, i) for i in range(count)
                    ]
                    cell = cells.setdefault(
                        (skill, days),
                        {
                            "missions": 0,
                            "qualities": [],
                            "fees": [],
                            "best_qualities": [],
                            "best_full": [],
                            "single_full": [],
                        },
                    )
                    cell["missions"] += 1
                    for record in discoveries:
                        cell["qualities"].append(record["quality_floor"])
                        cell["fees"].append(record["signing_fee"])
                    best = max(discoveries, key=lambda r: (r["quality_floor"], -r["signing_fee"]))
                    cell["best_qualities"].append(best["quality_floor"])
                    cell["best_full"].append(
                        mission_full_cost(
                            mission_fee,
                            [r["signing_fee"] for r in discoveries],
                            [discoveries.index(best)],
                        )
                    )
                    if days == 4:
                        cell["single_full"].append(mission_fee + discoveries[0]["signing_fee"])
                    if skill == 5 and position == "MC" and seed == seeds[0]:
                        examples.append(
                            {
                                "skill": skill,
                                "days": days,
                                "mission_fee": mission_fee,
                                "candidates": discoveries,
                            }
                        )
    rows: list[dict[str, Any]] = []
    for (skill, days), cell in sorted(cells.items()):
        qualities = cell["qualities"]
        strong = sum(1 for q in qualities if q >= STRONG_Q)
        elite = sum(1 for q in qualities if q >= ELITE_Q)
        rows.append(
            {
                "scout_skill": skill,
                "days": days,
                "missions": cell["missions"],
                "discoveries": len(qualities),
                "mission_fee": F.scout_mission_fee(skill, days),
                "quality_quantiles": quantiles([float(q) for q in qualities]),
                "signing_fee_quantiles": quantiles([float(f) for f in cell["fees"]]),
                "strong_share_q12": strong / len(qualities),
                "elite_share_q15": elite / len(qualities),
                "mean_best_quality": sum(cell["best_qualities"]) / len(cell["best_qualities"]),
                "mean_best_full_cost": sum(cell["best_full"]) / len(cell["best_full"]),
            }
        )
    # Fast-vs-long comparison at scout skill 5: one 4-day mission (sign its
    # single candidate) against one 16-day mission (sign the best of four).
    by_key = {(r["scout_skill"], r["days"]): r for r in rows}
    comparison = {}
    for skill in SCOUT_SKILLS:
        short = by_key[(skill, 4)]
        long = by_key[(skill, 16)]
        comparison[str(skill)] = {
            "fast_4d_mean_full": sum(cells[(skill, 4)]["single_full"])
            / len(cells[(skill, 4)]["single_full"]),
            "long_16d_mean_best_full": long["mean_best_full_cost"],
            "long_16d_mean_best_quality": long["mean_best_quality"],
            "short_mean_quality": short["quality_quantiles"]["median"],
            "elapsed_days": {"fast": 4, "long": 16},
        }
    # Affordability: lower bound ignores wages/upkeep/windows/squad cap;
    # realistic adds 20 cycles of existing obligations plus youth wages.
    median_single_full = by_key[(5, 4)]["signing_fee_quantiles"]["median"] + F.scout_mission_fee(
        5, 4
    )
    void_wages: list[int] = []
    for position in SCOUT_POSITIONS:
        record = _discovery_record(seeds[0], 0, 5, position, 4, 0)
        void_wages.append(
            F.player_wage(tuple(record["skills8"]), record["age"], position, record["positions"])
        )
    median_youth_wage = sorted(void_wages)[len(void_wages) // 2]
    wages_mid = 40_000  # Documented tier-5 band midpoint; ledger below uses real wages.
    obligations = TOTAL_CYCLES * (
        wages_mid + F.staff_wage("head_coach") + F.stadium_upkeep(BASE_CAPACITY)
    )
    conservative_income = (
        TOTAL_CYCLES
        * (sponsor_terms(TIER, None, SUPPORTERS).cycle_rate + F.merchandise_income(SUPPORTERS))
        + 9 * F.ticket_income(HOME_CROWD, BASE_CAPACITY)
        + F.season_prize(TIER, 10)
    )
    discretionary = F.INITIAL_CAPITAL - obligations + conservative_income
    affordability = {
        "median_upfront_full_cost": median_single_full,
        "upfront_cost_note": "Mission + median signing at skill 5/4d is a LOWER BOUND: "
        "windows, squad cap and youth wages are ignored here.",
        "median_youth_wage": median_youth_wage,
        "count_vs_600k_upfront_only": F.INITIAL_CAPITAL // int(median_single_full),
        "discretionary_turnover_cash": discretionary,
        "count_with_turnover_obligations": max(
            0, discretionary // int(median_single_full + TOTAL_CYCLES * median_youth_wage)
        ),
        "count_note": "The turnover count exceeds the upfront-only count because "
        "season income (sponsor/merch/tickets/prize) funds part of the wages; "
        "both counts ignore windows and the 25-player cap.",
        "squad_cap_note": "Active+incoming player cap is 25 (application/candidates.py); "
        "a 20-player squad fits at most 5 signings without sales. Signings need an "
        "open window and a free slot; counts above ignore both and stay lower bounds.",
    }
    return {
        "seeds": seeds,
        "skills": list(SCOUT_SKILLS),
        "positions": list(SCOUT_POSITIONS),
        "durations": list(SCOUT_DAYS),
        "strong_band": STRONG_Q,
        "elite_band": ELITE_Q,
        "cells": rows,
        "fast_vs_long_skill": comparison,
        "affordability": affordability,
        "examples_skill5_mc": examples,
    }


def _development_run(age: int, staff: int, busy: bool, seed: int) -> dict[str, Any]:
    player = TrainingPlayer(
        UUID(int=1),
        age,
        "MC",
        dict.fromkeys(SKILLS, 10),
        dict.fromkeys(SKILLS, 20),
        dict.fromkeys(PROGRESS_KEYS, 0),
        6,
        0,
    )
    budget = GrowthBudget(age)
    days = 0
    matches = 0
    for day in range(80):
        # Eight offseason dates, then eighteen four-day cycles in one age-season.
        phase = (day - 8) % 4
        cycle = (day - 8) // 4
        league_game = day >= 8 and phase == 3
        cup_load = day >= 8 and (
            (phase == 0 and cycle < (17 if busy else 2)) or (busy and phase == 1 and cycle < 17)
        )
        if league_game:
            matches += 1
            appearance = develop_appearance(player, 90, {"attack_coach": staff}, budget)
            growth = appearance.growth
            player = TrainingPlayer(
                player.id,
                player.age,
                player.position,
                growth.skills,
                player.caps,
                growth.progress,
                player.form,
                appearance.experience,
                False,
                player.morale,
                player.leadership,
                player.leadership_progress,
            )
            budget = growth.budget
        elif not cup_load:
            # Free UTC date with an explicit plan; cup/friendly load days and
            # matches never grant growth in the eligible runtime.
            days += 1
            result = train_player(
                player,
                DEV_FOCUS,
                DEV_INTENSITY,
                seed=f"audit:{seed}:{age}:{staff}:{'busy' if busy else 'free'}:{day}",
                trainer_skill=staff,
                head_skill=staff,
                physio_skill=staff,
                budget=budget,
            )
            budget = result.budget
            player = TrainingPlayer(
                player.id,
                player.age,
                player.position,
                result.skills,
                player.caps,
                result.progress,
                result.form,
                result.experience,
                False,
                result.morale,
                result.leadership,
                result.leadership_progress,
            )
    return {
        "age": age,
        "staff": staff,
        "calendar": "busy" if busy else "free",
        "training_days": days,
        "league_matches": matches,
        "training_bps": budget.used["training"],
        "match_bps": budget.used["match"],
        "total_bps": budget.used["training"] + budget.used["match"],
        "leadership_bps": budget.used["leadership"],
        "stamina_gain": player.skills["stamina"] - 10,
        "pace_gain": player.skills["pace"] - 10,
        "stamina_fraction": player.progress["stamina"],
        "pace_fraction": player.progress["pace"],
        "morale": player.morale,
        "budget_used": dict(budget.used),
    }


def development_section(seeds: list[int]) -> dict[str, Any]:
    seed = seeds[0]
    runs = [
        _development_run(age, staff, busy, seed)
        for age in DEV_AGES
        for staff in DEV_STAFF
        for busy in (False, True)
    ]
    # Ceiling demo: potential caps stop growth before the budget does.
    capped = TrainingPlayer(
        UUID(int=2),
        18,
        "MC",
        dict.fromkeys(SKILLS, 12),
        dict.fromkeys(SKILLS, 12),
        dict.fromkeys(PROGRESS_KEYS, 0),
        6,
        0,
    )
    capped_budget = GrowthBudget(18)
    for day in range(20):
        result = train_player(
            capped,
            DEV_FOCUS,
            DEV_INTENSITY,
            seed=f"audit:ceiling:{day}",
            trainer_skill=18,
            head_skill=18,
            physio_skill=18,
            budget=capped_budget,
        )
        capped_budget = result.budget
        capped = TrainingPlayer(
            capped.id,
            capped.age,
            capped.position,
            result.skills,
            capped.caps,
            result.progress,
            capped.form,
            capped.experience,
            False,
            capped.morale,
            capped.leadership,
            capped.leadership_progress,
        )
    ceiling_demo = {
        "start_skills": 12,
        "caps": 12,
        "training_days": 20,
        "training_bps": capped_budget.used["training"],
        "stamina_gain": capped.skills["stamina"] - 12,
        "note": "Caps bind first: 20 strong sessions grant nothing when "
        "skills already sit at their potential.",
    }
    # Transfer demo: reusing the same age-season budget preserves accounting.
    # Use a lightly-trained run so the remaining allowance is nonzero.
    finished = next(
        r for r in runs if r["age"] == 34 and r["staff"] == 2 and r["calendar"] == "free"
    )
    transferred = GrowthBudget(18, dict(finished["budget_used"]))
    skills = dict.fromkeys(SKILLS, 10)
    caps = dict.fromkeys(SKILLS, 20)
    progress = dict.fromkeys([*SKILLS, "experience"], 0)
    requested = {"playmaking": 30_000}
    growth = grant_growth(skills, caps, progress, transferred, requested, source="training")
    remaining_training = SOURCE_LIMITS["training"] - transferred.used["training"]
    transfer_demo = {
        "used_before_transfer": dict(transferred.used),
        "requested_training_bps": 30_000,
        "granted_after_transfer": sum(growth.granted.values()),
        "remaining_training_allowance": remaining_training,
        "note": "A transfer reuses the same age-season budget object: prior use is "
        "preserved, and the grant is capped by the remaining allowance, not reset.",
    }
    recovery_table = [
        {
            "physio": physio,
            "hours_to_full": round(ready_in(7320, 0, daily_recovery(9, 27, physio)) / 3600, 2),
        }
        for physio in (0, 1, 5, 10, 15, 20)
    ]
    return {
        "focus": DEV_FOCUS.value,
        "intensity": DEV_INTENSITY.value,
        "ages": list(DEV_AGES),
        "staff_skills": list(DEV_STAFF),
        "runs": runs,
        "ceiling_demo": ceiling_demo,
        "transfer_demo": transfer_demo,
        "recovery_hours_from_7320bps": recovery_table,
        "recovery_note": "Recovery is real elapsed hours (recovery-v1); adjacent UTC "
        "dates do not guarantee 24 hours of rest.",
    }


def run_audit(seeds: list[int], commit: str = "") -> dict[str, Any]:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Seeds must be nonempty and unique")
    for seed in seeds:
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise ValueError("Seeds must be integers from 0 through 2**63 - 1")
    return {
        "tool": TOOL_VERSION,
        "commit": commit,
        "policies": dict(POLICY_VERSIONS),
        "seeds": list(seeds),
        "turnover": {"season_days": 72, "offseason_days": 8, "cycles": TOTAL_CYCLES},
        "budget": budget_section(),
        "scout": scout_section(seeds),
        "development": development_section(seeds),
        "assumptions": [
            "Tier-5 starting club, 2500 supporters, 20 players from the pinned "
            "world (key gameplay-balance-audit, seed 0); wages vary per club, the "
            "report keeps the min/max band and the full series of the first club.",
            "Fixed seasonal sponsor: opening tenth-place base for cycles 1-18, "
            "renewal by final place/destination tier for cycles 19-20; the season "
            "prize lands with cycle 18 only, never earlier.",
            "Expenses are charged before income each cycle; a negative pre-income "
            "minimum is a liquidity shortfall forecast, the runtime keeps cash >= 0 "
            "with debt instead.",
            "Scout discoveries are private: declined/unsigned people never enter "
            "the public market and earn no resale income in this audit.",
            "Development runs use train_player/develop_appearance/grant_growth "
            "directly; only the 18 official league appearances grant match growth, "
            "cup load days and friendlies grant none.",
            "Training injuries are discarded to upper-bound growth; morale/ "
            "leadership/form draws stay seeded and deterministic.",
        ],
    }


def content_hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def render_markdown(content: dict[str, Any], digest: str) -> str:
    lines = [
        "# Gameplay balance audit (offline, AND-71)",
        "",
        "Reproducible offline audit of current formulas and scenarios. No balance",
        "changes, no database, no Telegram, no network. Cup runtime mechanics are",
        "not connected; cup income below is a flagged hypothetical.",
        "",
        f"- Tool: `{content['tool']}`, content hash: `{digest}`",
        f"- Commit: `{content['commit'] or 'unpinned'}`",
        "- Policies: "
        + ", ".join(f"{name}={version}" for name, version in content["policies"].items()),
        f"- Seeds: {content['seeds']}",
        "",
        "## 1. Full-turnover budget (20 cycles / 80 days)",
        "",
        "Tier-5 club, 2500 supporters, fixed seasonal sponsor, prize with cycle 18,",
        "expenses before income. `min_pre` is the lowest pre-income cash (liquidity",
        "trough); `shortfall` is its first cycle, if any.",
        "",
        "| Scenario | Staff/cycle | Upfront | Min pre-income | Ending (min–max) | Shortfall |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for row in content["budget"]["scenarios"]:
        flag = " (hypothetical)" if row["hypothetical"] else ""
        shortfall = row["first_shortfall_cycle"] or "—"
        lines.append(
            f"| {row['label']}{flag} | {row['staff_per_cycle']:,} | {row['upfront']:,} | "
            f"{row['min_pre_income']:,} | {row['ending_min']:,}–{row['ending_max']:,} | "
            f"{shortfall} |"
        )
    lines += [
        "",
        "## 2. Scout rejuvenation (skill 5 shown; full cells in JSON)",
        "",
        "One active mission; 4/8/12/16 days yield 1/2/3/4 candidates at the end,",
        "no weekly intermediates. The mission fee is paid once in full; each",
        "signing adds its own separate fee and four-day wage.",
        "",
        "| Scout | 4d: mission + median signing | 16d: mission + best of 4 | Best quality 16d |",
        "|---|---:|---:|---:|",
    ]
    cells = {(r["scout_skill"], r["days"]): r for r in content["scout"]["cells"]}
    for skill in SCOUT_SKILLS:
        short = cells[(skill, 4)]
        long = cells[(skill, 16)]
        fast_full = int(short["mission_fee"] + short["signing_fee_quantiles"]["median"])
        best_full = int(long["mean_best_full_cost"])
        lines.append(
            f"| {skill} | {fast_full:,} | {best_full:,} | {long['mean_best_quality']:.2f} |"
        )
    afford = content["scout"]["affordability"]
    lines += [
        "",
        f"Median upfront full cost (mission + signing, lower bound): "
        f"{int(afford['median_upfront_full_cost']):,}. "
        f"Youths vs 600K upfront-only: {afford['count_vs_600k_upfront_only']}. "
        f"With 20 cycles of wages/coach/upkeep and season income: "
        f"{afford['count_with_turnover_obligations']} (squad cap 25 fits at most 5 "
        "without sales; windows also apply).",
        "",
        "## 3. Development speed (physical/medium; runs in JSON)",
        "",
        "Free calendar (~60 sessions) vs busy cup calendar (~28 sessions); only 18",
        "league appearances grant match growth. Values are fractional points.",
        "",
        "| Age | Staff | Free total | Busy total | Match (both) |",
        "|---|---:|---:|---:|---:|",
    ]
    runs = {(r["age"], r["staff"], r["calendar"]): r for r in content["development"]["runs"]}
    for age in DEV_AGES:
        for staff in (2, 10, 18):
            free = runs[(age, staff, "free")]
            busy = runs[(age, staff, "busy")]
            lines.append(
                f"| {age} | {staff} | {free['total_bps'] / 10000:.3f} "
                f"({free['stamina_gain']}/{free['pace_gain']} whole) | "
                f"{busy['total_bps'] / 10000:.3f} | {free['match_bps'] / 10000:.3f} |"
            )
    lines += [
        "",
        "## Limits of this audit",
        "",
        "No DB throughput/p95, no worker/ledger races, no HDC infrastructure, no",
        "connected cup mechanics, no real match results (pinned calendar income).",
        "See `assumptions` in the JSON.",
    ]
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline gameplay-balance audit (AND-71)")
    parser.add_argument("--seed", type=int, default=7, help="Base seed (default 7)")
    parser.add_argument(
        "--count", type=int, default=4, help="Consecutive seeds from --seed (default 4)"
    )
    parser.add_argument(
        "--seeds",
        type=str,
        default=None,
        help="Explicit comma-separated seeds; overrides --seed/--count",
    )
    parser.add_argument("--commit", type=str, default="", help="Commit SHA recorded in the report")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/gameplay-balance-audit"),
        help="Report stem; writes .json and .md",
    )
    parser.add_argument("--max-findings", type=int, default=5000, help="Discovery budget cap")
    return parser.parse_args(argv)


def resolve_seeds(args: argparse.Namespace) -> list[int]:
    if args.seeds is not None:
        seeds = [int(part.strip()) for part in args.seeds.split(",") if part.strip()]
    else:
        if args.count < 1 or args.count > 64:
            raise ValueError("--count must be from 1 through 64")
        seeds = [args.seed + i for i in range(args.count)]
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Seeds must be nonempty and unique")
    for seed in seeds:
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise ValueError("Seeds must be integers from 0 through 2**63 - 1")
    total = len(seeds) * len(SCOUT_SKILLS) * len(SCOUT_POSITIONS) * sum(SCOUT_DAYS) // 4
    if total > args.max_findings:
        raise ValueError(f"Requested {total} discoveries exceed --max-findings {args.max_findings}")
    return seeds


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        seeds = resolve_seeds(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    started = time.perf_counter()
    content = run_audit(seeds, commit=args.commit)
    duration_s = time.perf_counter() - started
    digest = content_hash(content)
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    report = dict(content)
    report["content_hash"] = digest
    output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    output.with_suffix(".md").write_text(render_markdown(content, digest), encoding="utf-8")
    print(
        json.dumps(
            {
                "seeds": seeds,
                "content_hash": digest,
                "duration_s": round(duration_s, 3),
                "database_written": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
