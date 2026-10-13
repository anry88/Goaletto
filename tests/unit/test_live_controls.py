"""Versioned instructions, replay and full ownership/match/window callback frames."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.live_commands import LiveScreen
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.bot import live_views
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_commands import apply_commands
from goaletto.domain.match_engine import advance_tick
from goaletto.domain.match_ratings import sector_ratings
from goaletto.domain.match_types import CommandKind, Instruction, MatchCommand, Side


def v2():
    old = demo_match(7)
    return replace(
        old,
        engine_version="regulation-v2",
        policy=replace(
            old.policy,
            version="match-policy-v2",
            base_chance_rate_bps=0,
            yellow_bps=0,
            direct_red_bps=0,
            foul_bps=0,
            injury_bps=0,
        ),
    )


def test_v1_checkpoint_keeps_original_golden_hash_and_v2_restarts_exactly():
    old, _ = simulate_match(demo_match(7))
    assert state_hash(old) == "ae4002fb500d7769e280a9939d3f345f78e935b6c1b1be6a3f0bb792850c6841"
    assert "instructions" not in state_document(old)["home"]
    state = v2()
    person = state.home.lineup[-1].player_id
    command = MatchCommand(
        uuid4(),
        Side.HOME,
        1,
        CommandKind.INSTRUCTION,
        player_id=person,
        instruction=Instruction.MOVE_LEFT,
    )
    result = advance_tick(state, 1, (command,))
    restored = restore_state(state_document(result.state))
    assert restored == result.state
    assert simulate_match(restored) == simulate_match(result.state)
    assert any(
        e.kind == "instruction" and e.player_id == person and e.detail == "move_left"
        for e in result.events
    )


def test_instructions_change_ratings_and_three_changes_are_enforced():
    state = v2()
    person = state.home.lineup[-1].player_id
    baseline = sector_ratings(state.home, state.policy)
    cmd = MatchCommand(
        uuid4(),
        Side.HOME,
        1,
        CommandKind.INSTRUCTION,
        player_id=person,
        instruction=Instruction.MOVE_LEFT,
    )
    changed, accepted, _ = apply_commands(state, (cmd,), tick=1, halftime=False)
    ratings = sector_ratings(changed.home, changed.policy)
    assert (
        ratings.attack_left > baseline.attack_left
        and ratings.attack_center < baseline.attack_center
    )
    for value in (Instruction.MOVE_RIGHT, Instruction.DROP_BACK):
        cmd = replace(cmd, id=uuid4(), instruction=value)
        changed, accepted, rejected = apply_commands(changed, (cmd,), tick=1, halftime=False)
        assert accepted and not rejected
    cmd = replace(cmd, id=uuid4(), instruction=Instruction.RUNS_BEHIND)
    _, accepted, rejected = apply_commands(changed, (cmd,), tick=1, halftime=False)
    assert not accepted and rejected == ((cmd.id, "instruction_limit"),)
    keeper = state.home.lineup[0].player_id
    cmd = replace(cmd, id=uuid4(), player_id=keeper)
    _, accepted, rejected = apply_commands(state, (cmd,), tick=1, halftime=False)
    assert not accepted and rejected[0][1] == "player_unavailable"


def test_live_callbacks_fit_telegram_limit_and_pin_complete_context():
    state = v2()
    now = datetime(2026, 10, 17, 18, tzinfo=UTC)
    screen = LiveScreen(
        uuid4(),
        state.home.snapshot.club_id,
        "<Клуб>",
        uuid4(),
        state.match_id,
        state,
        state,
        Side.HOME,
        30,
        False,
        now + timedelta(seconds=60),
        True,
        (),
    )
    for action, args in [
        ("s", (20, 20)),
        ("i", (20, 4)),
        ("t", (5,)),
        ("m", (4,)),
        ("r", ()),
        ("p", (4,)),
    ]:
        data = live_views.callback(screen, action, *args)
        assert len(data.encode()) <= 64
        assert live_views.parse_callback(data) == (
            action,
            args,
            screen.ownership_id,
            screen.match_id,
            30,
            False,
        )
    text, keyboard = live_views.render(screen)
    assert "&lt;Klub&gt;" in text
    for row in keyboard.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
    for data in ("lv:s:1:2", "lv:p:99", "lv:s:01:2", "lv:x:0"):
        with pytest.raises(ValueError):
            live_views.parse_callback(data)
