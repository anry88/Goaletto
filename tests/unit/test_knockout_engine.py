"""Real V4 exposure, replay, neutral support and one-kick penalty transitions."""

from dataclasses import replace
from uuid import uuid4

import pytest

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.clock import MatchPhase
from goaletto.domain.knockout import KNOCKOUT_POLICY, KnockoutTie
from goaletto.domain.knockout_match import (
    KnockoutContext,
    advance_penalty,
    decision,
    start_extra_half,
)
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_engine import advance_tick, start_match, start_second_half
from goaletto.domain.match_types import CoachSnapshot, CommandKind, EnginePolicy, MatchCommand, Side


def cup_state(seed=7, *, final=True, quiet=True):
    old = demo_match(seed)
    coach = CoachSnapshot(None, 1)
    home = replace(old.home.snapshot, coach=coach)
    away = replace(old.away.snapshot, coach=coach)
    context = KnockoutContext(
        uuid4(),
        KnockoutTie(KNOCKOUT_POLICY, home.club_id, away.club_id, "single" if final else "two-leg"),
        "final" if final else "leg1",
    )
    policy = EnginePolicy(
        version="match-policy-v4",
        base_chance_rate_bps=0 if quiet else 8000,
        injury_bps=0,
        yellow_bps=0,
        direct_red_bps=0,
    )
    return start_match(
        uuid4(),
        seed,
        home,
        away,
        engine_version="regulation-v4",
        policy=policy,
        crowd=old.crowd,
        knockout=context,
    )


def regulation(state):
    for tick in range(1, 31):
        if tick == 16:
            state = start_second_half(state).state
        state = advance_tick(state, tick).state
    return state


def test_first_leg_draw_finishes_at_ninety_and_keeps_home_support():
    state = cup_state(final=False)
    end = regulation(state)
    assert end.phase == MatchPhase.FULL_TIME and end.game_minute == 90
    assert decision(end).status == "await_match"
    assert state.home.support_morale_points > 0
    assert restore_state(state_document(end)) == end


def test_neutral_final_really_plays_extra_time_and_penalties_without_extra_minutes():
    state = cup_state()
    assert state.home.support_morale_points == state.away.support_morale_points == 0
    state = regulation(state)
    assert state.phase == MatchPhase.EXTRA_BREAK
    assert not any(c.minutes_played > 90 for c in state.home.conditions)
    state = start_extra_half(state).state
    for tick in range(31, 41):
        if tick == 36:
            assert state.phase == MatchPhase.EXTRA_HALF_TIME
            state = start_extra_half(state).state
        state = advance_tick(state, tick).state
        assert restore_state(state_document(state)) == state
    assert state.phase == MatchPhase.PENALTIES
    assert state.game_minute == 120 and state.tick == 40
    exposure = tuple(c.minutes_played for c in state.home.conditions)
    energy = tuple(c.energy_bps for c in state.home.conditions)
    original = state
    for kick in range(1, 101):
        outcome = advance_penalty(state, kick)
        assert outcome == advance_penalty(state, kick)
        state = outcome.state
        assert restore_state(state_document(state)) == state
        assert tuple(c.minutes_played for c in state.home.conditions) == exposure
        assert tuple(c.energy_bps for c in state.home.conditions) == energy
        assert state.home.stats.goals == state.away.stats.goals == 0
        if state.phase == MatchPhase.FULL_TIME:
            break
    assert state.phase == MatchPhase.FULL_TIME and decision(state).winner is not None
    assert state_hash(simulate_match(original)[0]) == state_hash(state)
    from goaletto.application.live_commands import LiveScreen
    from goaletto.bot.live_views import render

    screen = LiveScreen(
        uuid4(),
        state.home.snapshot.club_id,
        "Club",
        uuid4(),
        state.match_id,
        state,
        None,
        Side.HOME,
        None,
        False,
        None,
        False,
        (),
    )
    text, _ = render(screen)
    assert "Общий счёт: 0:0" in text and "Победитель:" in text and "Пенальти:" in text


def test_substitution_at_extra_break_keeps_limits_and_affects_only_remaining_minutes():
    state = regulation(cup_state())
    outgoing, incoming = state.home.lineup[1].player_id, state.home.bench[0]
    command = MatchCommand(
        uuid4(),
        Side.HOME,
        31,
        CommandKind.SUBSTITUTION,
        outgoing_id=outgoing,
        incoming_id=incoming,
        halftime=True,
    )
    state = start_extra_half(state, (command,)).state
    assert state.home.substitutions == 1 and state.home.substitution_windows == ()
    end, _ = simulate_match(state)
    minutes = {c.player_id: c.minutes_played for c in end.home.conditions}
    assert minutes[outgoing] == 90 and minutes[incoming] == 30
    assert max(minutes.values()) == 120


def test_corrupt_extra_time_without_draw_rejected():
    state = regulation(cup_state(final=False))
    with pytest.raises(ValueError):
        restore_state({**state_document(state), "phase": "extra_break"})
