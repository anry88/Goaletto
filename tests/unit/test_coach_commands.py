"""Coach repertoire and finite command budgets survive checkpoints and projections."""

from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.match_codec import restore_state, state_document, state_hash, state_json
from goaletto.domain.match_commands import apply_commands
from goaletto.domain.match_engine import start_match
from goaletto.domain.match_types import (
    CoachSnapshot,
    CommandKind,
    Instruction,
    MatchCommand,
    Mentality,
    Side,
    Tactic,
)


def coached(skill=1):
    demo = demo_match(7)
    return start_match(
        demo.match_id,
        demo.seed,
        replace(demo.home.snapshot, coach=CoachSnapshot(UUID(int=90), skill)),
        replace(demo.away.snapshot, coach=CoachSnapshot(None)),
        engine_version="regulation-v3",
    )


def command(index, kind, **kwargs):
    return MatchCommand(UUID(int=index), Side.HOME, 1, kind, **kwargs)


@pytest.mark.parametrize("skill", [1, 5, 9, 13, 17])
def test_all_tier_limits_reserve_apply_and_restore(skill):
    state = coached(skill)
    abilities = state.home.snapshot.coach.abilities
    player = state.home.lineup[1].player_id
    instructions = tuple(
        command(
            n + 1, CommandKind.INSTRUCTION, player_id=player, instruction=list(Instruction)[n % 5]
        )
        for n in range(abilities.instruction_changes + 1)
    )
    state, accepted, rejected = apply_commands(state, instructions, tick=1, halftime=False)
    assert len(accepted) == abilities.instruction_changes
    assert rejected == ((instructions[-1].id, "instruction_limit"),)
    moods = tuple(
        command(
            100 + n,
            CommandKind.MENTALITY,
            mentality=Mentality.ATTACK if n % 2 == 0 else Mentality.DEFENCE,
        )
        for n in range(abilities.mentality_changes + 1)
    )
    state, accepted, rejected = apply_commands(state, moods, tick=1, halftime=False)
    assert len(accepted) == abilities.mentality_changes
    assert rejected[-1][1] == "mentality_limit"
    if abilities.tactic_changes:
        tactics = tuple(
            command(
                200 + n, CommandKind.TACTIC, tactic=Tactic.DEFENCE if n % 2 == 0 else Tactic.NORMAL
            )
            for n in range(abilities.tactic_changes + 1)
        )
        state, accepted, rejected = apply_commands(state, tactics, tick=1, halftime=False)
        assert len(accepted) == abilities.tactic_changes
        assert rejected[-1][1] == "tactic_limit"
    if skill < 17:
        _, accepted, rejected = apply_commands(
            state,
            (command(400, CommandKind.TACTIC, tactic=Tactic.PRESSING),),
            tick=1,
            halftime=False,
        )
        assert not accepted and rejected[0][1] == "tactic_unavailable"
    restored = restore_state(state_json(state))
    assert restored == state
    assert state_hash(simulate_match(restored)[0]) == state_hash(simulate_match(state)[0])
    assert state.policy.max_substitutions == 5 and state.policy.max_windows == 3


def test_noop_and_retry_do_not_consume_and_codec_is_closed():
    state = coached()
    mood = command(1, CommandKind.MENTALITY, mentality=Mentality.ATTACK)
    state, _, _ = apply_commands(state, (mood,), tick=1, halftime=False)
    assert state.home.mentality_changes == 1
    repeated, accepted, rejected = apply_commands(state, (mood,), tick=1, halftime=False)
    assert repeated == state and not accepted and not rejected
    same = command(2, CommandKind.MENTALITY, mentality=Mentality.ATTACK)
    assert apply_commands(state, (same,), tick=1, halftime=False)[0].home.mentality_changes == 1
    document = state_document(state)
    document["home"]["snapshot"]["coach"]["skill"] = 21
    with pytest.raises(ValueError):
        restore_state(document)
    document = state_document(state)
    document["home"]["mentality_changes"] = 2
    with pytest.raises(ValueError):
        restore_state(document)
