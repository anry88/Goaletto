"""Offline batch validation of regulation matches (AND-71, offline part).

Runs bounded batches of pure ``regulation-v2`` simulations with fixed seeds and
writes one deterministic JSON report plus a human-readable Markdown summary
(and optionally a CSV of per-match rows).

Scope: existing v2 engine only. No PostgreSQL, no Telegram, no PNG loading and
no balance changes. Scenario policies override incident probabilities explicitly
and record them as scenario parameters; the domain engine is never modified.

Reproduction::

    python scripts/validate_match_batches.py --seed 7 --matches 8 \\
        --output work/match-batch-validation
    python scripts/validate_match_batches.py --seeds 7,8,9 --matches 3 \\
        --scenarios baseline,home_swap,scripted_commands --csv \\
        --output work/match-batch-validation
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from goaletto.application.match_simulation import club_snapshot
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import restore_state, state_hash, state_json
from goaletto.domain.match_engine import advance_tick, start_match, start_second_half
from goaletto.domain.match_types import (
    LIVE_ENGINE_VERSION,
    CommandKind,
    EnginePolicy,
    FieldSlot,
    HomeCrowd,
    Instruction,
    MatchCommand,
    MatchEvent,
    MatchState,
    Mentality,
    PlayerSnapshot,
    Side,
    Tactic,
    TeamSnapshot,
)
from goaletto.domain.world import StartingClub, WorldOptions, build_world

ENGINE_VERSION = LIVE_ENGINE_VERSION
POLICY_VERSION = "match-policy-v2"
WORLD_KEY = "match-batch-validation"
WORLD_SEED = 0
WORLD_KICKOFF = datetime(2026, 10, 17, 18, tzinfo=UTC)
DEFAULT_CROWD = HomeCrowd(stadium_capacity=5000, home_fans=2500)
CHECKPOINT_TICKS = (1, 14, 15, 16, 23, 29)
MAX_MATCHES_DEFAULT = 2000
TERMINAL_PHASES = {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}

CALM_POLICY = EnginePolicy(
    version=POLICY_VERSION, yellow_bps=0, direct_red_bps=0, injury_bps=0, foul_bps=0
)
INJURY_STORM_POLICY = EnginePolicy(
    version=POLICY_VERSION, yellow_bps=0, direct_red_bps=0, injury_bps=10000, foul_bps=0
)
RED_STORM_POLICY = EnginePolicy(
    version=POLICY_VERSION, yellow_bps=0, direct_red_bps=10000, injury_bps=0, foul_bps=0
)
SECOND_YELLOW_POLICY = EnginePolicy(
    version=POLICY_VERSION, yellow_bps=10000, direct_red_bps=0, injury_bps=0, foul_bps=0
)


@dataclass(frozen=True)
class ScenarioSpec:
    """Static scenario definition; parameters are recorded in the report."""

    name: str
    description: str
    params: dict[str, Any]


def base_players(club: StartingClub) -> list[PlayerSnapshot]:
    """Player snapshots identical to the shared offline adapter."""
    return list(club_snapshot(club).players)


def make_snapshot(
    club: StartingClub,
    players: list[PlayerSnapshot],
    *,
    lineup_count: int = 11,
    tactic: Tactic = Tactic.NORMAL,
    mentality: Mentality = Mentality.BALANCED,
) -> TeamSnapshot:
    lineup = tuple(FieldSlot(p.id, p.position) for p in players[:lineup_count])
    bench = tuple(p.id for p in players[lineup_count:])
    return TeamSnapshot(
        club_id=club.id,
        name=club.name,
        players=tuple(players),
        lineup=lineup,
        bench=bench,
        tactic=tactic,
        mentality=mentality,
    )


def pick_pair(world: Any, seed_index: int, *, offset: int = 0) -> tuple[Any, Any]:
    league = world.leagues[(seed_index + offset) % len(world.leagues)]
    clubs = league.clubs
    home = clubs[(seed_index * 2 + offset) % len(clubs)]
    away = clubs[(seed_index * 2 + 1 + offset) % len(clubs)]
    return home, away


def build_case(
    spec: ScenarioSpec, home_club: StartingClub, away_club: StartingClub
) -> tuple[TeamSnapshot, TeamSnapshot, EnginePolicy, HomeCrowd]:
    """Build engine snapshots for one scenario case (deterministic)."""
    name = spec.name
    home_players = base_players(home_club)
    away_players = base_players(away_club)
    if name == "short_squad_11":
        return (
            make_snapshot(home_club, home_players[:11]),
            make_snapshot(away_club, away_players[:11]),
            EnginePolicy(version=POLICY_VERSION),
            DEFAULT_CROWD,
        )
    if name == "bench_14":
        return (
            make_snapshot(home_club, home_players[:14]),
            make_snapshot(away_club, away_players[:14]),
            EnginePolicy(version=POLICY_VERSION),
            DEFAULT_CROWD,
        )
    if name == "unavailable_bench":
        home_players[11] = replace(home_players[11], available=False)
        return (
            make_snapshot(home_club, home_players),
            make_snapshot(away_club, away_players),
            EnginePolicy(version=POLICY_VERSION),
            DEFAULT_CROWD,
        )
    if name == "last_season_starter":
        home_players[9] = replace(home_players[9], last_season=True)
        return (
            make_snapshot(home_club, home_players),
            make_snapshot(away_club, away_players),
            CALM_POLICY,
            DEFAULT_CROWD,
        )
    if name == "roster_25_snapshot_20":
        extras = [
            replace(
                home_players[15 + i],
                id=uuid5(home_club.id, f"extra-reserve:{i}"),
                name=f"{home_players[15 + i].name} (res.)",
            )
            for i in range(5)
        ]
        roster = home_players + extras
        assert len(roster) == 25
        return (
            make_snapshot(home_club, roster[:20]),
            make_snapshot(away_club, away_players),
            EnginePolicy(version=POLICY_VERSION),
            DEFAULT_CROWD,
        )
    if name == "scripted_commands":
        return (
            make_snapshot(home_club, home_players),
            make_snapshot(away_club, away_players),
            CALM_POLICY,
            DEFAULT_CROWD,
        )
    if name == "injury_storm":
        return (
            make_snapshot(home_club, home_players),
            make_snapshot(away_club, away_players),
            INJURY_STORM_POLICY,
            DEFAULT_CROWD,
        )
    if name == "dismissal_red_storm":
        return (
            make_snapshot(home_club, home_players),
            make_snapshot(away_club, away_players),
            RED_STORM_POLICY,
            DEFAULT_CROWD,
        )
    if name == "second_yellow_storm":
        return (
            make_snapshot(home_club, home_players),
            make_snapshot(away_club, away_players),
            SECOND_YELLOW_POLICY,
            DEFAULT_CROWD,
        )
    return (
        make_snapshot(home_club, home_players),
        make_snapshot(away_club, away_players),
        EnginePolicy(version=POLICY_VERSION),
        DEFAULT_CROWD,
    )


def _command_id(match_id: UUID, *parts: str) -> UUID:
    return uuid5(match_id, "batch-command:" + ":".join(parts))


def plan_commands(
    scenario: str, state: MatchState
) -> tuple[dict[int, list[MatchCommand]], list[MatchCommand]]:
    """Scripted live commands for one match; empty means saved lineup only."""
    if scenario != "scripted_commands":
        return {}, []
    home, away = state.home, state.away
    h_line = [s.player_id for s in home.lineup]
    h_bench = list(home.bench)
    a_line = [s.player_id for s in away.lineup]
    a_bench = list(away.bench)
    match_id = state.match_id

    def sub(side: Side, out_id: UUID, in_id: UUID, tick: int) -> MatchCommand:
        return MatchCommand(
            _command_id(match_id, side.value, "sub", str(tick), str(out_id)[:8]),
            side,
            tick,
            CommandKind.SUBSTITUTION,
            outgoing_id=out_id,
            incoming_id=in_id,
        )

    live: dict[int, list[MatchCommand]] = {}
    # Two substitutions inside a single live window (tick 5).
    live[5] = [sub(Side.HOME, h_line[7], h_bench[0], 5), sub(Side.HOME, h_line[8], h_bench[1], 5)]
    for tick, tactic in (
        (10, Tactic.PRESSING),
        (11, Tactic.WINGS),
        (12, Tactic.DEFENCE),
    ):
        live[tick] = [
            MatchCommand(
                _command_id(match_id, "home", "tactic", str(tick)),
                Side.HOME,
                tick,
                CommandKind.TACTIC,
                tactic=tactic,
            )
        ]
    # Fourth tactic change exceeds the limit of three.
    live[13] = [
        MatchCommand(
            _command_id(match_id, "home", "tactic", "13"),
            Side.HOME,
            13,
            CommandKind.TACTIC,
            tactic=Tactic.NORMAL,
        )
    ]
    live[20] = [
        MatchCommand(
            _command_id(match_id, "home", "mentality", "20"),
            Side.HOME,
            20,
            CommandKind.MENTALITY,
            mentality=Mentality.ATTACK,
        )
    ]
    live[21] = [
        MatchCommand(
            _command_id(match_id, "home", "instruction", "21"),
            Side.HOME,
            21,
            CommandKind.INSTRUCTION,
            player_id=h_line[4],
            instruction=Instruction.LONG_SHOTS,
        )
    ]
    live[22] = [
        MatchCommand(
            _command_id(match_id, "home", "instruction", "22"),
            Side.HOME,
            22,
            CommandKind.INSTRUCTION,
            player_id=h_line[6],
            instruction=Instruction.MOVE_LEFT,
        )
    ]
    live[25] = [
        sub(Side.HOME, h_line[10], h_bench[3], 25),
        sub(Side.HOME, h_line[3], h_bench[4], 25),
        MatchCommand(
            _command_id(match_id, "away", "mentality", "25"),
            Side.AWAY,
            25,
            CommandKind.MENTALITY,
            mentality=Mentality.DEFENCE,
        ),
    ]
    # Instruction for the goalkeeper must be rejected.
    live[26] = [
        MatchCommand(
            _command_id(match_id, "home", "instruction", "26"),
            Side.HOME,
            26,
            CommandKind.INSTRUCTION,
            player_id=h_line[0],
            instruction=Instruction.RUNS_BEHIND,
        ),
        sub(Side.AWAY, a_line[7], a_bench[0], 26),
    ]
    # Sixth substitution exceeds the limit of five (limits are checked first).
    live[27] = [sub(Side.HOME, h_line[5], h_bench[5], 27)]
    halftime = [
        MatchCommand(
            _command_id(match_id, "home", "sub", "half"),
            Side.HOME,
            16,
            CommandKind.SUBSTITUTION,
            outgoing_id=h_line[9],
            incoming_id=h_bench[2],
            halftime=True,
        ),
        MatchCommand(
            _command_id(match_id, "home", "instruction", "half"),
            Side.HOME,
            16,
            CommandKind.INSTRUCTION,
            player_id=h_line[5],
            instruction=Instruction.RUNS_BEHIND,
            halftime=True,
        ),
    ]
    return live, halftime


def run_match(
    initial: MatchState,
    live: dict[int, list[MatchCommand]],
    halftime: list[MatchCommand],
) -> tuple[MatchState, list[MatchEvent], list[str], list[tuple[str, str]]]:
    """Drive a full regulation match; returns final state and event log."""
    state = initial
    events: list[MatchEvent] = []
    accepted: list[str] = []
    rejected: list[tuple[str, str]] = []
    while state.phase not in TERMINAL_PHASES:
        if state.phase == MatchPhase.HALF_TIME:
            result = start_second_half(state, tuple(halftime))
        else:
            result = advance_tick(state, state.tick + 1, tuple(live.get(state.tick + 1, ())))
        state = result.state
        events.extend(result.events)
        accepted.extend(str(c) for c in result.accepted_commands)
        rejected.extend((str(c), reason) for c, reason in result.rejected_commands)
    return state, events, accepted, rejected


def run_to_tick(
    initial: MatchState,
    boundary: int,
    live: dict[int, list[MatchCommand]],
    halftime: list[MatchCommand],
) -> tuple[MatchState, list[MatchEvent]]:
    """Advance to a checkpoint boundary (inclusive) for restart checks."""
    state = initial
    events: list[MatchEvent] = []
    while state.tick < boundary and state.phase not in TERMINAL_PHASES:
        if state.phase == MatchPhase.HALF_TIME:
            result = start_second_half(state, tuple(halftime))
        else:
            result = advance_tick(state, state.tick + 1, tuple(live.get(state.tick + 1, ())))
        state = result.state
        events.extend(result.events)
    return state, events


def event_counter(events: list[MatchEvent]) -> Counter[str]:
    return Counter(e.kind for e in events)


def stats_row(state: MatchState, events: list[MatchEvent]) -> dict[str, Any]:
    counts = event_counter(events)
    home, away = state.home, state.away
    return {
        "phase": state.phase.value,
        "tick": state.tick,
        "minute": state.game_minute,
        "termination_reason": state.termination_reason,
        "score_home": home.stats.goals,
        "score_away": away.stats.goals,
        "state_hash": state_hash(state),
        "events": len(events),
        "home_shots": home.stats.shots,
        "home_on_target": home.stats.on_target,
        "away_shots": away.stats.shots,
        "away_on_target": away.stats.on_target,
        "home_corners": home.stats.corners,
        "away_corners": away.stats.corners,
        "home_fouls": home.stats.fouls,
        "away_fouls": away.stats.fouls,
        "yellows": counts.get("yellow", 0),
        "reds": counts.get("red", 0),
        "injuries": counts.get("injury", 0),
        "auto_substitutions": counts.get("automatic_substitution", 0),
        "replacement_unavailable": counts.get("replacement_unavailable", 0),
        "home_subs": home.substitutions,
        "away_subs": away.substitutions,
        "home_windows": list(home.substitution_windows),
        "away_windows": list(away.substitution_windows),
        "home_tactic_changes": home.tactic_changes,
        "away_tactic_changes": away.tactic_changes,
        "home_instruction_changes": home.instruction_changes,
        "away_instruction_changes": away.instruction_changes,
    }


def quantiles(values: list[int]) -> dict[str, float]:
    if not values:
        return {"min": 0, "p25": 0, "median": 0, "p75": 0, "max": 0}
    ordered = sorted(values)
    n = len(ordered)

    def pick(rank: float) -> float:
        position = rank * (n - 1)
        low = int(position)
        high = min(n - 1, low + 1)
        frac = position - low
        return ordered[low] + (ordered[high] - ordered[low]) * frac

    return {
        "min": float(ordered[0]),
        "p25": pick(0.25),
        "median": pick(0.5),
        "p75": pick(0.75),
        "max": float(ordered[-1]),
    }


SCENARIOS: dict[str, ScenarioSpec] = {
    "baseline": ScenarioSpec(
        "baseline",
        "Comparable baseline squads, fixed saved lineup, no live commands.",
        {"pair_offset": 0, "crowd": "5000/2500", "policy": "match-policy-v2 defaults"},
    ),
    "home_swap": ScenarioSpec(
        "home_swap",
        "Same pairs as baseline with home/away sides swapped.",
        {"pair_offset": 0, "swapped": True, "crowd": "5000/2500"},
    ),
    "saved_lineup_no_manager": ScenarioSpec(
        "saved_lineup_no_manager",
        "Saved lineup without a manager: no rotation, training or LLM input.",
        {"pair_offset": 1, "crowd": "5000/2500", "accepted_commands_must_be_empty": True},
    ),
    "short_squad_11": ScenarioSpec(
        "short_squad_11",
        "Eleven selected players, empty bench.",
        {"participants": 11, "pair_offset": 2},
    ),
    "bench_14": ScenarioSpec(
        "bench_14",
        "Fourteen selected players with a short bench.",
        {"participants": 14, "pair_offset": 2},
    ),
    "unavailable_bench": ScenarioSpec(
        "unavailable_bench",
        "One bench player unavailable (injured); must never enter the field.",
        {"unavailable_bench_players": 1, "pair_offset": 3},
    ),
    "last_season_starter": ScenarioSpec(
        "last_season_starter",
        "Starter flagged last-season plays; trade ban is not a match ban.",
        {"last_season_starters": 1, "policy": "calm incidents", "pair_offset": 3},
    ),
    "roster_25_snapshot_20": ScenarioSpec(
        "roster_25_snapshot_20",
        "Club roster of 25 with only 20 engine snapshot participants.",
        {"club_roster": 25, "snapshot_participants": 20, "pair_offset": 4},
    ),
    "scripted_commands": ScenarioSpec(
        "scripted_commands",
        "Scripted subs/tactics/mentality/instructions with limits and halftime.",
        {"policy": "calm incidents", "halftime_batch": True, "pair_offset": 4},
    ),
    "injury_storm": ScenarioSpec(
        "injury_storm",
        "Forced injuries: injury-only auto substitutions and unavailable fallbacks.",
        {"policy": "injury_bps=10000, cards/fouls zero", "pair_offset": 5},
    ),
    "dismissal_red_storm": ScenarioSpec(
        "dismissal_red_storm",
        "Forced direct reds, leading to abandoned matches as a separate outcome.",
        {"policy": "direct_red_bps=10000, other incidents zero", "pair_offset": 5},
    ),
    "second_yellow_storm": ScenarioSpec(
        "second_yellow_storm",
        "Forced yellows: second-yellow dismissals without direct reds.",
        {"policy": "yellow_bps=10000, direct_red/injury/fouls zero", "pair_offset": 6},
    ),
}


def check_match_invariants(
    record: dict[str, Any],
    *,
    scenario: str,
    initial: MatchState,
    final: MatchState,
    events: list[MatchEvent],
    accepted: list[str],
    rejected: list[tuple[str, str]],
) -> list[tuple[str, bool, str]]:
    """Per-match correctness checks; returns (name, passed, detail) triples."""
    checks: list[tuple[str, bool, str]] = []
    if final.phase == MatchPhase.FULL_TIME:
        ok = final.tick == 30 and final.game_minute == 90
        checks.append(("full_time_30_ticks", ok, f"tick={final.tick} minute={final.game_minute}"))
    elif final.phase == MatchPhase.ABANDONED:
        try:
            advance_tick(final, final.tick + 1)
            frozen_ok = False
        except ValueError:
            frozen_ok = True
        ok = final.termination_reason == "insufficient_players" and final.tick < 30 and frozen_ok
        checks.append(("abandoned_separate_outcome", ok, f"tick={final.tick}"))
    for side_name in ("home", "away"):
        team = getattr(final, side_name)
        other = getattr(final, "away" if side_name == "home" else "home")
        removed = set(team.removed)
        field = {s.player_id for s in team.lineup}
        ok = not (field & removed)
        ok = ok and not any(
            c.injured or c.dismissed for c in team.conditions if c.player_id in field
        )
        checks.append((f"removed_never_returns_{side_name}", ok, record["match_id"][:8]))
        ok = (
            team.substitutions <= 5
            and len(team.substitution_windows) <= 3
            and team.tactic_changes <= 3
            and team.instruction_changes <= 3
        )
        checks.append((f"limits_hold_{side_name}", ok, f"subs={team.substitutions}"))
        on_target_ok = team.stats.on_target == team.stats.goals + other.stats.saves
        checks.append((f"on_target_consistency_{side_name}", on_target_ok, ""))
    ids = [e.id for e in events]
    checks.append(("event_ids_unique", len(set(ids)) == len(ids), f"events={len(ids)}"))
    if scenario == "saved_lineup_no_manager":
        ok = (
            not accepted
            and final.home.tactic == initial.home.tactic
            and final.away.tactic == initial.away.tactic
            and final.home.mentality == initial.home.mentality
            and final.away.mentality == initial.away.mentality
        )
        checks.append(("no_manager_no_rotation", ok, f"accepted={len(accepted)}"))
    if scenario == "scripted_commands":
        reasons = sorted(reason for _, reason in rejected)
        ok = reasons == ["player_unavailable", "substitution_limit", "tactic_limit"]
        checks.append(("scripted_limits_rejected", ok, f"rejected={reasons}"))
        checks.append(
            (
                "scripted_halftime_free_window",
                16 not in final.home.substitution_windows,
                f"windows={list(final.home.substitution_windows)}",
            )
        )
    if scenario == "last_season_starter":
        starter = initial.home.lineup[9].player_id
        conditions = {c.player_id: c for c in final.home.conditions}
        ok = conditions[starter].minutes_played == 90 and any(
            s.player_id == starter for s in final.home.lineup
        )
        checks.append(("last_season_may_play", ok, "trade ban is not a match ban"))
    if scenario == "unavailable_bench":
        missing = initial.home.bench[0]
        field = {s.player_id for s in final.home.lineup}
        ok = missing not in field
        checks.append(("unavailable_never_starts", ok, ""))
    if scenario in {"short_squad_11", "bench_14", "roster_25_snapshot_20"}:
        expected = {"short_squad_11": 11, "bench_14": 14}.get(scenario, 20)
        ok = len(initial.home.snapshot.players) == expected
        checks.append(("snapshot_participants_bounded", ok, f"participants={expected}"))
    return checks


def summarize_scenario(rows: list[dict[str, Any]]) -> dict[str, Any]:
    full = [r for r in rows if r["phase"] == "full_time"]
    abandoned = [r for r in rows if r["phase"] == "abandoned"]
    home_wins = sum(1 for r in rows if r["score_home"] > r["score_away"])
    draws = sum(1 for r in rows if r["score_home"] == r["score_away"])
    away_wins = sum(1 for r in rows if r["score_home"] < r["score_away"])
    totals = [r["score_home"] + r["score_away"] for r in rows]
    return {
        "matches": len(rows),
        "full_time": len(full),
        "abandoned": len(abandoned),
        "abandoned_share": (len(abandoned) / len(rows)) if rows else 0,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "goals_home": sum(r["score_home"] for r in rows),
        "goals_away": sum(r["score_away"] for r in rows),
        "total_goals_quantiles": quantiles(totals),
        "shots": sum(r["home_shots"] + r["away_shots"] for r in rows),
        "on_target": sum(r["home_on_target"] + r["away_on_target"] for r in rows),
        "yellows": sum(r["yellows"] for r in rows),
        "reds": sum(r["reds"] for r in rows),
        "injuries": sum(r["injuries"] for r in rows),
        "auto_substitutions": sum(r["auto_substitutions"] for r in rows),
        "replacement_unavailable": sum(r["replacement_unavailable"] for r in rows),
        "max_subs_used": max([max(r["home_subs"], r["away_subs"]) for r in rows] or [0]),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline batch validation of v2 matches")
    parser.add_argument("--seed", type=int, default=7, help="Base seed (default 7)")
    parser.add_argument(
        "--seeds",
        type=str,
        default=None,
        help="Explicit comma-separated seeds; overrides --seed/--matches sizing",
    )
    parser.add_argument("--matches", type=int, default=8, help="Matches per scenario")
    parser.add_argument(
        "--scenarios",
        type=str,
        default=None,
        help="Comma-separated scenario subset (default: all)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("work/match-batch-validation"),
        help="Report stem; writes .json and .md (plus .csv with --csv)",
    )
    parser.add_argument("--csv", action="store_true", help="Also write a per-match CSV")
    parser.add_argument(
        "--max-matches", type=int, default=MAX_MATCHES_DEFAULT, help="Total match budget cap"
    )
    return parser.parse_args(argv)


def resolve_seeds(args: argparse.Namespace) -> list[int]:
    if args.seeds is not None:
        seeds = [int(part.strip()) for part in args.seeds.split(",") if part.strip()]
        if not seeds:
            raise ValueError("--seeds must list at least one seed")
    else:
        if args.matches < 1:
            raise ValueError("--matches must be at least 1")
        seeds = [args.seed + i for i in range(args.matches)]
    for seed in seeds:
        if type(seed) is not int or not 0 <= seed < 2**63:
            raise ValueError("Seeds must be integers from 0 through 2**63 - 1")
    return seeds


def resolve_scenarios(args: argparse.Namespace) -> list[ScenarioSpec]:
    if args.scenarios is None:
        return list(SCENARIOS.values())
    names = [part.strip() for part in args.scenarios.split(",") if part.strip()]
    unknown = [name for name in names if name not in SCENARIOS]
    if unknown:
        raise ValueError(f"Unknown scenarios: {', '.join(unknown)}")
    return [SCENARIOS[name] for name in names]


def run_batch(
    seeds: list[int], specs: list[ScenarioSpec], *, checkpoint_matches: int = 3
) -> dict[str, Any]:
    """Execute every scenario; returns the deterministic report content."""
    world = build_world(WorldOptions(key=WORLD_KEY, seed=WORLD_SEED, first_kickoff=WORLD_KICKOFF))
    matches: list[dict[str, Any]] = []
    check_triples: list[tuple[str, bool, str]] = []
    replay_mismatches = 0
    checkpoint_mismatches = 0
    checkpoint_total = 0

    # One construction probe: an unavailable starter must be rejected upfront.
    try:
        probe_club, _ = pick_pair(world, 0, offset=0)
        players = base_players(probe_club)
        players[0] = replace(players[0], available=False)
        make_snapshot(probe_club, players)
        check_triples.append(("unavailable_starter_rejected", False, "no error raised"))
    except ValueError:
        check_triples.append(("unavailable_starter_rejected", True, ""))

    for spec in specs:
        offset = int(spec.params.get("pair_offset", 0))
        swapped = bool(spec.params.get("swapped", False))
        for index, seed in enumerate(seeds):
            home_club, away_club = pick_pair(world, index, offset=offset)
            if swapped:
                home_club, away_club = away_club, home_club
            home_snap, away_snap, policy, crowd = build_case(spec, home_club, away_club)
            match_id = uuid5(world.season_id, f"batch:{spec.name}:{seed}:{index}")
            initial = start_match(
                match_id,
                seed,
                home_snap,
                away_snap,
                policy=policy,
                crowd=crowd,
                engine_version=ENGINE_VERSION,
            )
            live, halftime = plan_commands(spec.name, initial)
            final, events, accepted, rejected = run_match(initial, live, halftime)
            # Identical inputs must replay bit-for-bit (hash plus event IDs).
            final_again, events_again, _, _ = run_match(initial, live, halftime)
            replay_ok = state_hash(final_again) == state_hash(final) and [
                e.id for e in events_again
            ] == [e.id for e in events]
            if not replay_ok:
                replay_mismatches += 1
            check_triples.append(
                (f"replay_determinism:{spec.name}:{seed}", replay_ok, str(match_id)[:8])
            )
            record = stats_row(final, events)
            record.update(
                {
                    "scenario": spec.name,
                    "seed": seed,
                    "match_id": str(match_id),
                    "home": home_snap.name,
                    "away": away_snap.name,
                    "home_participants": len(home_snap.players),
                    "away_participants": len(away_snap.players),
                    "accepted_commands": len(accepted),
                    "rejected_commands": [[c, reason] for c, reason in rejected],
                    "replay_ok": replay_ok,
                }
            )
            matches.append(record)
            check_triples.extend(
                check_match_invariants(
                    record,
                    scenario=spec.name,
                    initial=initial,
                    final=final,
                    events=events,
                    accepted=accepted,
                    rejected=rejected,
                )
            )
            # Checkpoint/restart on several boundaries for the first matches.
            if spec.name in {"baseline", "scripted_commands"} and index < checkpoint_matches:
                for boundary in CHECKPOINT_TICKS:
                    checkpoint_total += 1
                    reached, reached_events = run_to_tick(initial, boundary, live, halftime)
                    restored = restore_state(state_json(reached))
                    codec_ok = restored == reached
                    resumed_events: list[MatchEvent] = []
                    resumed = restored
                    resumed_rejected: list[tuple[str, str]] = []
                    while resumed.phase not in TERMINAL_PHASES:
                        if resumed.phase == MatchPhase.HALF_TIME:
                            result = start_second_half(resumed, tuple(halftime))
                        else:
                            tick = resumed.tick + 1
                            result = advance_tick(resumed, tick, tuple(live.get(tick, ())))
                        resumed = result.state
                        resumed_events.extend(result.events)
                        resumed_rejected.extend(
                            (str(c), reason) for c, reason in result.rejected_commands
                        )
                    # The checkpoint run emits a strict prefix of the reference event
                    # sequence (halftime command events belong to the resume, not the
                    # prefix), so the remainder is compared by position, not by tick.
                    expected_remaining = tuple(events[len(reached_events) :])
                    events_ok = tuple(resumed_events) == expected_remaining
                    hash_ok = state_hash(resumed) == state_hash(final)
                    counters_ok = (
                        resumed.home.substitutions == final.home.substitutions
                        and resumed.away.substitutions == final.away.substitutions
                        and resumed.home.tactic_changes == final.home.tactic_changes
                        and resumed.home.instruction_changes == final.home.instruction_changes
                        and resumed.phase == final.phase
                    )
                    ok = codec_ok and events_ok and hash_ok and counters_ok
                    if not ok:
                        checkpoint_mismatches += 1
                    check_triples.append(
                        (
                            f"checkpoint_restart:{spec.name}:{seed}@{boundary}",
                            ok,
                            f"hash={hash_ok} events={events_ok} counters={counters_ok}",
                        )
                    )

    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for record in matches:
        by_scenario.setdefault(record["scenario"], []).append(record)
    scenario_reports = {
        name: {"matches": len(rows), "aggregate": summarize_scenario(rows)}
        for name, rows in by_scenario.items()
    }

    assertions = [
        {"name": name, "passed": passed, "detail": detail} for name, passed, detail in check_triples
    ]
    failed = [a for a in assertions if not a["passed"]]

    # Statistical observations only: no required goal rates on small samples.
    observations: list[str] = []
    for name, report in scenario_reports.items():
        agg = report["aggregate"]
        observations.append(
            f"{name}: {report['matches']} matches, "
            f"home/draw/away {agg['home_wins']}/{agg['draws']}/{agg['away_wins']}, "
            f"goals {agg['goals_home']}:{agg['goals_away']}, "
            f"abandoned {agg['abandoned']}."
        )
    if "baseline" in scenario_reports and "home_swap" in scenario_reports:
        base = scenario_reports["baseline"]["aggregate"]
        swapped = scenario_reports["home_swap"]["aggregate"]
        observations.append(
            "Same-seed baseline vs home_swap (descriptive, not a tactic effect): "
            f"baseline home/draw/away {base['home_wins']}/{base['draws']}/{base['away_wins']}, "
            f"swapped {swapped['home_wins']}/{swapped['draws']}/{swapped['away_wins']}."
        )

    return {
        "metadata": {
            "engine_version": ENGINE_VERSION,
            "policy_version": POLICY_VERSION,
            "world_key": WORLD_KEY,
            "world_seed": WORLD_SEED,
            "seeds": seeds,
            "matches_per_scenario": len(seeds),
            "scenarios": [
                {"name": s.name, "description": s.description, "params": s.params} for s in specs
            ],
            "checkpoint_ticks": list(CHECKPOINT_TICKS),
            "sample_size": len(matches),
            "replay_mismatches": replay_mismatches,
            "checkpoint_total": checkpoint_total,
            "checkpoint_mismatches": checkpoint_mismatches,
        },
        "scenarios": scenario_reports,
        "matches": matches,
        "assertions": assertions,
        "failed_assertions": len(failed),
        "observations": observations,
    }


def content_hash(content: dict[str, Any]) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def render_markdown(content: dict[str, Any], digest: str) -> str:
    meta = content["metadata"]
    lines = [
        "# Match batch validation (offline)",
        "",
        "Deterministic offline batch over the existing regulation-v2 engine. "
        "No PostgreSQL, Telegram, PNG loading or balance changes.",
        "",
        f"- Engine: `{meta['engine_version']}`, policy: `{meta['policy_version']}`",
        f"- World: `{meta['world_key']}` (seed {meta['world_seed']})",
        f"- Seeds: {meta['seeds']}",
        f"- Matches per scenario: {meta['matches_per_scenario']}, "
        f"total sample: {meta['sample_size']}",
        f"- Checkpoint ticks: {meta['checkpoint_ticks']}",
        f"- Content hash: `{digest}`",
        "",
        "## Scenarios",
        "",
        "| Scenario | Matches | Full time | Abandoned | Home/Draw/Away | Goals H:A |",
        "|---|---:|---:|---:|---|---|",
    ]
    for spec in meta["scenarios"]:
        report = content["scenarios"][spec["name"]]
        agg = report["aggregate"]
        lines.append(
            f"| {spec['name']} | {report['matches']} | {agg['full_time']} | "
            f"{agg['abandoned']} | {agg['home_wins']}/{agg['draws']}/{agg['away_wins']} | "
            f"{agg['goals_home']}:{agg['goals_away']} |"
        )
    lines += [
        "",
        "## Correctness assertions",
        "",
        f"Failed: {content['failed_assertions']} of {len(content['assertions'])}.",
        "",
    ]
    for assertion in content["assertions"]:
        if assertion["name"].startswith(("replay_determinism", "checkpoint_restart")):
            continue
        mark = "pass" if assertion["passed"] else "FAIL"
        detail = f" — {assertion['detail']}" if assertion["detail"] else ""
        lines.append(f"- [{mark}] `{assertion['name']}`{detail}")
    lines += [
        "",
        f"- Replay mismatches: {meta['replay_mismatches']}; "
        f"checkpoint mismatches: {meta['checkpoint_mismatches']} "
        f"of {meta['checkpoint_total']} restarts.",
        "",
        "## Statistical observations (not assertions)",
        "",
    ]
    lines += [f"- {note}" for note in content["observations"]]
    lines += [
        "",
        "Small samples carry no required goal rates; scenario contrasts with shared "
        "seeds are descriptive and must not be read as tactic effects.",
        "",
        "## Limits",
        "",
        "This is an offline engine/sensitivity batch, not a load run: no PostgreSQL, "
        "no HDC worker, no Telegram delivery. Large result files belong in `work/`, "
        "not in Git.",
    ]
    return "\n".join(lines) + "\n"


CSV_FIELDS = [
    "scenario",
    "seed",
    "match_id",
    "home",
    "away",
    "phase",
    "tick",
    "score_home",
    "score_away",
    "state_hash",
    "events",
    "home_shots",
    "home_on_target",
    "away_shots",
    "away_on_target",
    "yellows",
    "reds",
    "injuries",
    "auto_substitutions",
    "home_subs",
    "away_subs",
    "accepted_commands",
    "replay_ok",
]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        seeds = resolve_seeds(args)
        specs = resolve_scenarios(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    total = len(seeds) * len(specs)
    if total > args.max_matches:
        print(
            f"error: requested {total} matches exceed --max-matches {args.max_matches}",
            file=sys.stderr,
        )
        return 2
    started = time.perf_counter()
    content = run_batch(seeds, specs)
    duration_s = time.perf_counter() - started
    digest = content_hash(content)

    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    json_path = output.with_suffix(".json")
    md_path = output.with_suffix(".md")
    report = dict(content)
    report["content_hash"] = digest
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_markdown(content, digest), encoding="utf-8")
    csv_path = None
    if args.csv:
        csv_path = output.with_suffix(".csv")
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(content["matches"])

    sizes = {p.name: p.stat().st_size for p in [json_path, md_path] if p.exists()}
    if csv_path is not None and csv_path.exists():
        sizes[csv_path.name] = csv_path.stat().st_size
    summary = {
        "scenarios": len(specs),
        "matches": len(content["matches"]),
        "failed_assertions": content["failed_assertions"],
        "replay_mismatches": content["metadata"]["replay_mismatches"],
        "checkpoint_mismatches": content["metadata"]["checkpoint_mismatches"],
        "content_hash": digest,
        "duration_s": round(duration_s, 3),
        "output_bytes": sizes,
        "database_written": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if content["failed_assertions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
