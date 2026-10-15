"""Pinned knockout context and real extra-time/penalty transitions for engine V4."""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from goaletto.domain.clock import MatchPhase
from goaletto.domain.knockout import (
    KnockoutTie,
    MatchResult,
    MatchSegment,
    PenaltyKick,
    PenaltyShootout,
    TieDecision,
    apply_penalty_kick,
    decide_shootout,
    decide_tie,
)

if TYPE_CHECKING:
    from goaletto.domain.match_types import MatchCommand, MatchState, TickResult

BREAK_PHASES = frozenset({MatchPhase.EXTRA_BREAK, MatchPhase.EXTRA_HALF_TIME})
PLAY_PHASES = frozenset({MatchPhase.EXTRA_FIRST_HALF, MatchPhase.EXTRA_SECOND_HALF})


@dataclass(frozen=True)
class KnockoutContext:
    tie_id: UUID
    tie: KnockoutTie
    segment: MatchSegment
    first_match: MatchResult | None = None
    regulation: MatchResult | None = None
    shootout: PenaltyShootout | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tie_id, UUID) or not isinstance(self.tie, KnockoutTie):
            raise ValueError("Invalid knockout identity")
        expected = ("final",) if self.tie.tie_format == "single" else ("leg1", "leg2")
        if self.segment not in expected:
            raise ValueError("Invalid knockout segment")
        if (self.segment == "leg2") != (self.first_match is not None):
            raise ValueError("Only the return leg needs the first leg result")
        if self.first_match:
            previous = decide_tie(tie=self.tie, first_match=self.first_match)
            if previous.next_segment != "leg2":
                raise ValueError("Return leg requires a completed first match")
        if self.regulation and self.regulation.segment != self.segment:
            raise ValueError("Regulation segment changed")


def decision(state: "MatchState") -> TieDecision:
    k = state.knockout
    if k is None:
        raise ValueError("No knockout context")
    extra = None
    if state.tick >= 40:
        if k.regulation is None or k.segment == "leg1":
            raise ValueError("Extra time needs a completed drawn regulation")
        extra = MatchResult(
            "final-et" if k.segment == "final" else "leg2-et",
            state.home.snapshot.club_id,
            state.away.snapshot.club_id,
            state.home.stats.goals - k.regulation.home_goals,
            state.away.stats.goals - k.regulation.away_goals,
            True,
        )
    return decide_tie(
        tie=k.tie,
        first_match=k.first_match if k.segment == "leg2" else k.regulation,
        second_match=k.regulation if k.segment == "leg2" else None,
        extra_time=extra,
        shootout=k.shootout,
    )


def complete_segment(state: "MatchState") -> "MatchState":
    """Called by the real tick engine only at 90/120 minutes."""
    from goaletto.domain.match_engine import _random

    k = state.knockout
    assert k is not None
    if state.tick == 30:
        regulation = MatchResult(
            k.segment,
            state.home.snapshot.club_id,
            state.away.snapshot.club_id,
            state.home.stats.goals,
            state.away.stats.goals,
            True,
        )
        state = replace(state, knockout=replace(k, regulation=regulation))
    outcome = decision(state)
    if outcome.status == "await_extra_time":
        return replace(state, phase=MatchPhase.EXTRA_BREAK)
    if outcome.status == "await_penalties":
        first = (
            k.tie.club_a_id
            if _random(state, 40, "penalty-first").randrange(2) == 0
            else k.tie.club_b_id
        )
        shootout = PenaltyShootout(k.tie.club_a_id, k.tie.club_b_id, first, ())
        return replace(state, phase=MatchPhase.PENALTIES, knockout=replace(k, shootout=shootout))
    return replace(state, phase=MatchPhase.FULL_TIME)


def start_extra_half(
    state: "MatchState", commands: tuple["MatchCommand", ...] = ()
) -> "TickResult":
    from goaletto.domain.match_commands import apply_commands
    from goaletto.domain.match_engine import _command_events, validate_state
    from goaletto.domain.match_types import MatchEvent, TickResult

    validate_state(state)
    if state.phase not in BREAK_PHASES:
        raise ValueError("Extra half must start from its break")
    first = state.phase == MatchPhase.EXTRA_BREAK
    tick = 31 if first else 36
    new, accepted, rejected = apply_commands(state, commands, tick=tick, halftime=True)
    events = _command_events(new, accepted, state.tick, state.game_minute, f"extra-half:{tick}")
    new = replace(new, phase=MatchPhase.EXTRA_FIRST_HALF if first else MatchPhase.EXTRA_SECOND_HALF)
    events.append(
        MatchEvent(
            uuid5(state.match_id, f"extra-start:{tick}"),
            "extra_time_started" if first else "extra_second_half_started",
            state.tick,
            state.game_minute,
        )
    )
    validate_state(new)
    return TickResult(new, tuple(events), tuple(c.id for c in accepted), rejected)


def advance_penalty(state: "MatchState", kick_number: int) -> "TickResult":
    """One actual, replayable kick. No limit or coin-toss winner in sudden death."""
    from goaletto.domain.match_engine import _random, validate_state
    from goaletto.domain.match_types import MatchEvent, Side, TickResult

    validate_state(state)
    k = state.knockout
    if state.phase != MatchPhase.PENALTIES or k is None or k.shootout is None:
        raise ValueError("Shootout is not active")
    result = decide_shootout(k.shootout)
    if type(kick_number) is not int or kick_number != result.next_kick_number:
        raise ValueError("Only the next penalty may execute")
    assert result.next_kicker_id is not None
    home = result.next_kicker_id == state.home.snapshot.club_id
    team, opponent = (state.home, state.away) if home else (state.away, state.home)
    # Reduce to equal eligible numbers. Injured/dismissed/substituted players
    # have already left the field. Stable skill order repeats only after a round.
    count = min(len(team.lineup), len(opponent.lineup))
    players = {p.id: p for p in team.snapshot.players}
    order = sorted(
        (s.player_id for s in team.lineup), key=lambda p: (-players[p].skills.set_pieces, str(p))
    )
    own_keeper = next(s.player_id for s in team.lineup if s.position == "GK")
    if own_keeper not in order[:count]:
        order = order[: count - 1] + [own_keeper]
    else:
        order = order[:count]
    taken = result.taken_a if result.next_kicker_id == k.tie.club_a_id else result.taken_b
    kicker = players[order[taken % count]]
    keeper_id = next(s.player_id for s in opponent.lineup if s.position == "GK")
    keeper = next(p for p in opponent.snapshot.players if p.id == keeper_id)
    chance = max(
        6000, min(9000, 7500 + 150 * (kicker.skills.set_pieces - keeper.skills.goalkeeping))
    )
    scored = _random(state, 40, f"penalty:{kick_number}").randrange(10000) < chance
    shootout = apply_penalty_kick(
        k.shootout, PenaltyKick(kick_number, result.next_kicker_id, scored)
    )
    end = decide_shootout(shootout)
    new = replace(
        state,
        knockout=replace(k, shootout=shootout),
        phase=MatchPhase.FULL_TIME if end.status == "decided" else MatchPhase.PENALTIES,
    )
    events = [
        MatchEvent(
            uuid5(state.match_id, f"penalty:{kick_number}"),
            "penalty_scored" if scored else "penalty_missed",
            40,
            120,
            side=Side.HOME if home else Side.AWAY,
            player_id=kicker.id,
            detail=str(kick_number),
        )
    ]
    if end.status == "decided":
        events.append(MatchEvent(uuid5(state.match_id, "knockout:full-time"), "full_time", 40, 120))
    validate_state(new)
    return TickResult(new, tuple(events))
