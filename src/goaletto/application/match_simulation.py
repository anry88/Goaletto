"""Offline adapters for the pure engine; no database, art generation or delivery."""

from datetime import UTC, datetime
from uuid import uuid5

from goaletto.domain.clock import MatchPhase
from goaletto.domain.knockout_match import BREAK_PHASES, advance_penalty, start_extra_half
from goaletto.domain.match_engine import advance_tick, start_match, start_second_half
from goaletto.domain.match_types import (
    FieldSlot,
    HomeCrowd,
    MatchEvent,
    MatchSkills,
    MatchState,
    PlayerSnapshot,
    TeamSnapshot,
)
from goaletto.domain.starter_squads import LEGACY_STARTER_POLICY
from goaletto.domain.world import StartingClub, WorldOptions, build_world


def club_snapshot(club: StartingClub) -> TeamSnapshot:
    return TeamSnapshot(
        club_id=club.id,
        name=club.name,
        players=tuple(
            PlayerSnapshot(
                p.id,
                p.name,
                p.position,
                MatchSkills(*p.skills[:8]),
                age=p.age,
                last_season=p.retirement.is_last_season,
            )
            for p in club.players
        ),
        lineup=tuple(FieldSlot(p.id, p.position) for p in club.players[:11]),
        bench=tuple(p.id for p in club.players[11:]),
    )


def demo_match(seed: int = 7) -> MatchState:
    world = build_world(
        WorldOptions(
            key="match-demo",
            seed=0,
            first_kickoff=datetime(2000, 1, 1, 18, tzinfo=UTC),
            naming_policy="legacy-v1",
            starter_policy=LEGACY_STARTER_POLICY,
        )
    )
    home, away = world.leagues[0].clubs[:2]
    return start_match(
        uuid5(world.season_id, "demo-match"),
        seed,
        club_snapshot(home),
        club_snapshot(away),
        crowd=HomeCrowd(stadium_capacity=5000, home_fans=2500),
    )


def simulate_match(state: MatchState) -> tuple[MatchState, tuple[MatchEvent, ...]]:
    events: list[MatchEvent] = []
    while state.phase not in {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}:
        result = (
            start_second_half(state)
            if state.phase == MatchPhase.HALF_TIME
            else start_extra_half(state)
            if state.phase in BREAK_PHASES
            else advance_penalty(state, len(state.knockout.shootout.kicks) + 1)
            if state.phase == MatchPhase.PENALTIES and state.knockout and state.knockout.shootout
            else advance_tick(state, state.tick + 1)
        )
        state = result.state
        events.extend(result.events)
    return state, tuple(events)
