"""Instant offline simulation, including restart from a JSON checkpoint."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_engine import advance_tick, start_second_half
from goaletto.domain.match_types import MatchEvent


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate a regulation match offline")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--resume", type=Path)
    parser.add_argument(
        "--ticks", type=int, help="Stop at a specific regulation tick, for restart checks"
    )
    parser.add_argument("--output", type=Path, help="Write state and event report JSON")
    args = parser.parse_args()
    state = (
        restore_state(json.loads(args.resume.read_text())["state"])
        if args.resume
        else demo_match(args.seed)
    )
    events: list[MatchEvent] = []
    if args.ticks is None:
        state, finished_events = simulate_match(state)
        events.extend(finished_events)
    else:
        if not state.tick <= args.ticks <= 30:
            parser.error("--ticks must be between the checkpoint tick and 30")
        while state.tick < args.ticks and state.phase != MatchPhase.ABANDONED:
            result = (
                start_second_half(state)
                if state.phase == MatchPhase.HALF_TIME
                else advance_tick(state, state.tick + 1)
            )
            state = result.state
            events.extend(result.events)
    report = {
        "state": state_document(state),
        "state_hash": state_hash(state),
        "events": [asdict(event) for event in events],
        "database_written": False,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "engine_version": state.engine_version,
                "tick": state.tick,
                "minute": state.game_minute,
                "phase": state.phase.value,
                "home": state.home.snapshot.name,
                "away": state.away.snapshot.name,
                "score": [state.home.stats.goals, state.away.stats.goals],
                "state_hash": state_hash(state),
                "events": len(events),
                "database_written": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
