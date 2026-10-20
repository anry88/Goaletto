"""Deterministic 80-day credit stress, current pure formulas; no DB or fake match results."""

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from goaletto.domain.academy import academy_upkeep
from goaletto.domain.calendar import league_calendar
from goaletto.domain.club_valuation import POLICY, bankrupt, facilities
from goaletto.domain.finances import (
    INITIAL_CAPITAL,
    STADIUM_CAPACITIES,
    merchandise_income,
    player_fee,
    player_wage,
    season_prize,
    stadium_upkeep,
    staff_fee,
    staff_wage,
    ticket_income,
)
from goaletto.domain.sponsorship import sponsor_terms
from goaletto.domain.world import WorldOptions, build_world


def audit() -> dict[str, Any]:
    first = datetime(2026, 10, 24, 18, tzinfo=UTC)
    world = build_world(WorldOptions(key="credit-stress-v1", seed=0, first_kickoff=first))
    summaries: dict[str, dict[str, int]] = defaultdict(
        lambda: {"scenarios": 0, "bankrupt": 0, "minimum_cash": 2**63 - 1}
    )
    baseline = []
    checks = 0
    unaffordable = 0
    for league in world.leagues:
        schedule = league_calendar(
            season_id=world.season_id,
            league_id=league.id,
            clubs=tuple(c.id for c in league.clubs),
            first_kickoff=first,
            seed=0,
        )
        homes = {
            c.id: {f.round_number for f in schedule if f.home_club_id == c.id} for c in league.clubs
        }
        for club in league.clubs:
            players = sum(player_fee(p.skills[:8], p.age, p.position) for p in club.players)
            wages = sum(player_wage(p.skills[:8], p.age, p.position) for p in club.players)
            for capacity in STADIUM_CAPACITIES:
                for level in range(1, 6):
                    stadium, academy = facilities(capacity, level)
                    for coach in (1, 10, 20):
                        staff = staff_fee("head_coach", coach, 50)
                        assets = players + staff + stadium + academy
                        upfront = stadium + academy - 400_000 + (staff if coach != 1 else 0)
                        out = (
                            wages
                            + staff_wage("head_coach", coach, 50)
                            + stadium_upkeep(capacity)
                            + academy_upkeep(level)
                        )
                        for crowd in (0, 2500):
                            for opening in ("capital", "0", "50", "90", "100"):
                                for shock in (0, 25):
                                    if opening == "capital" and upfront > INITIAL_CAPITAL:
                                        unaffordable += 1
                                        continue
                                    cash = (
                                        INITIAL_CAPITAL - upfront
                                        if opening == "capital"
                                        else -(assets * int(opening) // 100)
                                    )
                                    low, failed = cash, bankrupt(cash, assets)
                                    for cycle in range(1, 21):
                                        if failed:
                                            break
                                        # Runtime credits sponsor/merch before charges.
                                        income = sponsor_terms(
                                            league.tier, None, 2500
                                        ).cycle_rate + merchandise_income(2500)
                                        if cycle <= 18 and cycle in homes[club.id]:
                                            income += ticket_income(crowd, capacity)
                                        if cycle == 18:
                                            income += season_prize(league.tier, 10)
                                        cash += income - out
                                        low = min(low, cash)
                                        final_assets = assets - (
                                            players * shock // 100 if cycle >= 19 else 0
                                        )
                                        failed = bankrupt(cash, final_assets)
                                        checks += 1
                                    key = f"{opening}; postseason_player_value_drop={shock}%"
                                    row = summaries[key]
                                    row["scenarios"] += 1
                                    row["bankrupt"] += int(failed)
                                    row["minimum_cash"] = min(row["minimum_cash"], low)
                                    if (
                                        opening == "capital"
                                        and shock == 0
                                        and capacity == 5000
                                        and level == 1
                                        and coach == 1
                                    ):
                                        baseline.append(
                                            {
                                                "tier": league.tier,
                                                "crowd": crowd,
                                                "minimum_cash": low,
                                                "ending_cash": cash,
                                                "bankrupt": failed,
                                            }
                                        )
    assert not any(row["bankrupt"] for row in baseline)
    return {
        "tool": "club-credit-stress-v1",
        "policy": POLICY,
        "cycles": 20,
        "days": 80,
        "scenarios": sum(r["scenarios"] for r in summaries.values()),
        "cycle_checks": checks,
        "unaffordable_capital_scenarios_excluded": unaffordable,
        "groups": dict(sorted(summaries.items())),
        "baseline": {
            "clubs_and_attendance_cases": len(baseline),
            "bankrupt": 0,
            "minimum_cash": min(r["minimum_cash"] for r in baseline),
            "ending_cash_min": min(r["ending_cash"] for r in baseline),
        },
        "assumptions": [
            (
                "50 baseline clubs; 5 stadium levels; 5 academy levels; coach skills 1/10/20; "
                "0/2500 attendance; starting capital or 0/50/90/100% debt."
            ),
            (
                "Home dates come from the real pure league calendar. No fabricated persisted "
                "matches, results or receipts."
            ),
            (
                "Fixed players/skills/ages and tenth-place prize; no transfer/scout/training "
                "growth or cup income. Baseline includes academy upkeep."
            ),
            (
                "Optional 25% player-asset drop at cycle 19 is an explicit collateral stress, "
                "not a simulated retirement result."
            ),
            (
                "Purchases/bids require free cash. Capital scenarios exclude upgrades above "
                "600K available cash. Debt stress assumes already-owned assets and debt from "
                "wages/upkeep, not purchases financed by collateral."
            ),
            (
                "Insolvency in aggressive scenarios is expected. This report checks "
                "arithmetic and baseline survival, not final gameplay balance or HDC "
                "capacity."
            ),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/reports/club-credit-2026-10-20"))
    args = parser.parse_args()
    report = audit()
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True)
    report["content_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# Club credit stress · 2026-10-20",
        "",
        f"Policy: {POLICY}. {report['scenarios']} scenarios, "
        f"{report['cycle_checks']} cycle checks.",
        "",
        f"Baseline: {report['baseline']}",
        "",
        "| Opening | Scenarios | Insolvent | Lowest cash |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {key} | {r['scenarios']} | {r['bankrupt']} | {r['minimum_cash']} |"
        for key, r in report["groups"].items()
    ]
    lines += [
        "",
        *[f"- {a}" for a in report["assumptions"]],
        "",
        f"Content SHA256: {report['content_sha256']}",
    ]
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("scenarios", "cycle_checks", "baseline", "content_sha256")}
        )
    )


if __name__ == "__main__":
    main()
