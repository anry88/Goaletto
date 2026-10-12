from collections import Counter
from dataclasses import replace
from uuid import uuid5

import pytest

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import restore_state, state_document, state_hash, state_json
from goaletto.domain.match_engine import advance_tick, crowd_morale, start_match, start_second_half
from goaletto.domain.match_ratings import effective_skill, position_factor, sector_ratings
from goaletto.domain.match_types import (
    CommandKind,
    EnginePolicy,
    HomeCrowd,
    MatchCommand,
    MatchSkills,
    Mentality,
    Side,
    Tactic,
)


def quiet(state):
    return replace(
        state, policy=EnginePolicy(yellow_bps=0, direct_red_bps=0, injury_bps=0, foul_bps=0)
    )


def to_tick(state, tick):
    while state.tick < tick:
        state = (
            start_second_half(state).state
            if state.phase == MatchPhase.HALF_TIME
            else advance_tick(state, state.tick + 1).state
        )
    return state


def sub(state, outgoing, incoming, tick, *, halftime=False, key="sub"):
    return MatchCommand(
        uuid5(state.match_id, key),
        Side.HOME,
        tick,
        CommandKind.SUBSTITUTION,
        outgoing_id=outgoing,
        incoming_id=incoming,
        halftime=halftime,
    )


def test_golden_replay_and_immutable_inputs() -> None:
    initial = demo_match(7)
    before = state_json(initial)
    end, events = simulate_match(initial)
    assert state_json(initial) == before
    assert state_hash(end) == "ae4002fb500d7769e280a9939d3f345f78e935b6c1b1be6a3f0bb792850c6841"
    assert (end.home.stats.goals, end.away.stats.goals) == (1, 2)
    assert len(events) == len({e.id for e in events}) == 57
    assert (end, events) == simulate_match(initial)
    assert (end.tick, end.game_minute, end.phase) == (30, 90, MatchPhase.FULL_TIME)


@pytest.mark.parametrize("tick", [1, 14, 15, 16, 23, 29])
def test_restart_at_checkpoint_has_exact_remaining_events(tick: int) -> None:
    initial = demo_match(7)
    checkpoint = to_tick(initial, tick)
    restored = restore_state(state_json(checkpoint))
    assert restored == checkpoint
    end, remaining = simulate_match(restored)
    expected, expected_events = simulate_match(initial)
    assert state_hash(end) == state_hash(expected)
    expected_remaining = tuple(
        e
        for e in expected_events
        if e.tick > tick or (e.kind == "second_half_started" and tick == 15)
    )
    assert remaining == expected_remaining


def test_half_time_requires_an_explicit_transition_and_rest_is_not_repeated() -> None:
    state = to_tick(quiet(demo_match()), 15)
    with pytest.raises(ValueError, match="half time"):
        advance_tick(state, 16)
    second = start_second_half(state).state
    assert second.phase == MatchPhase.SECOND_HALF and second.game_minute == 45
    assert start_second_half(second).state == second
    assert start_second_half(second).events == ()


def test_duplicate_or_out_of_order_tick_never_produces_a_second_goal() -> None:
    state = advance_tick(demo_match(), 1).state
    for wrong_tick in (1, 3, True, 31):
        with pytest.raises(ValueError):
            advance_tick(state, wrong_tick)
    completed, _ = simulate_match(state)
    with pytest.raises(ValueError):
        advance_tick(completed, 31)


def test_substitutes_five_players_in_one_window_and_never_reenters_removed_player() -> None:
    state = quiet(demo_match())
    commands = tuple(
        sub(state, state.home.lineup[i].player_id, state.home.bench[i], 1, key=f"sub-{i}")
        for i in range(5)
    )
    result = advance_tick(state, 1, commands)
    assert result.state.home.substitutions == 5
    assert result.state.home.substitution_windows == (1,)
    assert len(result.accepted_commands) == 5
    impossible = sub(
        result.state,
        result.state.home.lineup[6].player_id,
        result.state.home.bench[0],
        2,
        key="sixth",
    )
    rejected = advance_tick(result.state, 2, (impossible,))
    assert rejected.rejected_commands == ((impossible.id, "substitution_limit"),)
    reentry = sub(
        result.state,
        result.state.home.lineup[0].player_id,
        result.state.home.removed[0],
        2,
        key="reentry",
    )
    assert advance_tick(result.state, 2, (reentry,)).rejected_commands == (
        (reentry.id, "player_unavailable"),
    )


def test_three_substitution_windows_and_halftime_is_free() -> None:
    state = quiet(demo_match())
    for tick in range(1, 4):
        command = sub(
            state,
            state.home.lineup[tick].player_id,
            state.home.bench[0],
            tick,
            key=f"window-{tick}",
        )
        state = advance_tick(state, tick, (command,)).state
    command = sub(
        state, state.home.lineup[5].player_id, state.home.bench[0], 4, key="fourth-window"
    )
    result = advance_tick(state, 4, (command,))
    assert result.rejected_commands == ((command.id, "window_limit"),)
    state = to_tick(result.state, 15)
    half_sub = sub(
        state,
        state.home.lineup[5].player_id,
        state.home.bench[0],
        16,
        halftime=True,
        key="half-sub",
    )
    result = start_second_half(state, (half_sub,))
    assert result.accepted_commands == (half_sub.id,)
    assert result.state.home.substitutions == 4
    assert result.state.home.substitution_windows == (1, 2, 3)


def test_command_id_replay_and_wrong_tick_are_safe() -> None:
    state = quiet(demo_match())
    command = MatchCommand(
        uuid5(state.match_id, "mentality"),
        Side.HOME,
        1,
        CommandKind.MENTALITY,
        mentality=Mentality.ATTACK,
    )
    first = advance_tick(state, 1, (command,))
    second = advance_tick(first.state, 2, (command,))
    assert second.accepted_commands == () and second.rejected_commands == ()
    assert second.state.home.mentality == Mentality.ATTACK
    stale = replace(command, id=uuid5(state.match_id, "stale"))
    assert advance_tick(first.state, 2, (stale,)).rejected_commands == (
        (stale.id, "stale_command"),
    )


def test_tactic_limit_and_mentality_are_separate() -> None:
    state = quiet(demo_match())
    for tick, tactic in enumerate((Tactic.PRESSING, Tactic.WINGS, Tactic.DEFENCE), start=1):
        command = MatchCommand(
            uuid5(state.match_id, f"tactic-{tick}"),
            Side.HOME,
            tick,
            CommandKind.TACTIC,
            tactic=tactic,
        )
        state = advance_tick(state, tick, (command,)).state
    command = MatchCommand(
        uuid5(state.match_id, "fourth-tactic"),
        Side.HOME,
        4,
        CommandKind.TACTIC,
        tactic=Tactic.NORMAL,
    )
    result = advance_tick(state, 4, (command,))
    assert result.rejected_commands == ((command.id, "tactic_limit"),)
    assert result.state.home.tactic_changes == 3


def test_fatigue_depends_on_stamina_and_pressing_not_the_bench() -> None:
    state = quiet(demo_match())
    home_snapshot = replace(
        state.home.snapshot,
        players=tuple(
            replace(p, skills=replace(p.skills, stamina=20)) for p in state.home.snapshot.players
        ),
    )
    away_snapshot = replace(
        state.away.snapshot,
        players=tuple(
            replace(p, skills=replace(p.skills, stamina=10)) for p in state.away.snapshot.players
        ),
        tactic=Tactic.PRESSING,
    )
    initial = start_match(
        state.match_id, state.seed, home_snapshot, away_snapshot, policy=state.policy
    )
    end, _ = simulate_match(initial)
    home = {c.player_id: c for c in end.home.conditions}
    away = {c.player_id: c for c in end.away.conditions}
    assert (
        home[end.home.lineup[0].player_id].energy_bps
        > away[end.away.lineup[0].player_id].energy_bps
    )
    assert all(home[p].energy_bps == 10000 and home[p].minutes_played == 0 for p in end.home.bench)
    assert all(home[s.player_id].minutes_played == 90 for s in end.home.lineup)


def test_reds_never_trigger_substitutions_and_abandonment_does_not_award_a_score() -> None:
    state = replace(
        demo_match(),
        policy=EnginePolicy(yellow_bps=0, direct_red_bps=10000, injury_bps=0, foul_bps=0),
    )
    end, events = simulate_match(state)
    assert end.phase == MatchPhase.ABANDONED
    assert end.termination_reason == "insufficient_players"
    assert end.tick < 30
    assert end.home.substitutions == end.away.substitutions == 0
    assert all(e.kind != "automatic_substitution" for e in events)
    assert (
        max(
            sum(c.dismissed for c in end.home.conditions),
            sum(c.dismissed for c in end.away.conditions),
        )
        == 5
    )
    with pytest.raises(ValueError):
        advance_tick(end, end.tick + 1)


def test_second_yellow_removes_the_player() -> None:
    state = replace(
        demo_match(),
        policy=EnginePolicy(yellow_bps=10000, direct_red_bps=0, injury_bps=0, foul_bps=0),
    )
    end, events = simulate_match(state)
    assert any(e.kind == "red" and e.detail == "second_yellow" for e in events)
    assert all(c.dismissed for c in end.home.conditions + end.away.conditions if c.yellows == 2)
    assert end.home.substitutions == end.away.substitutions == 0


def test_injury_replacements_follow_bench_order_and_stop_at_limits() -> None:
    state = replace(
        demo_match(),
        policy=EnginePolicy(yellow_bps=0, direct_red_bps=0, injury_bps=10000, foul_bps=0),
    )
    end, events = simulate_match(state)
    assert any(e.kind == "automatic_substitution" for e in events)
    assert any(e.kind == "replacement_unavailable" for e in events)
    assert end.home.substitutions <= 5 and len(end.home.substitution_windows) <= 3
    assert all(
        c.player_id not in {s.player_id for s in end.home.lineup}
        for c in end.home.conditions
        if c.injured
    )


def test_position_and_form_modifiers_affect_sector_strength() -> None:
    state = demo_match()
    player = state.home.snapshot.players[0]
    assert position_factor(player, "GK") == 10000
    assert position_factor(replace(player, alternatives=("FC",)), "FC") == 8500
    assert position_factor(player, "FC") == 6000
    low = replace(
        state.home,
        snapshot=replace(
            state.home.snapshot,
            players=tuple(replace(p, form=1) for p in state.home.snapshot.players),
        ),
    )
    assert sector_ratings(low).midfield < sector_ratings(state.home).midfield


def test_home_crowd_scales_with_attendance_and_capacity_only_limits_attendance() -> None:
    policy = EnginePolicy()
    assert crowd_morale(HomeCrowd(), policy) == 0
    assert crowd_morale(HomeCrowd(5000, 2500), policy) == 3
    assert crowd_morale(HomeCrowd(5000, 5000), policy) == 6
    assert crowd_morale(HomeCrowd(50000, 5000), policy) == 6
    assert crowd_morale(HomeCrowd(50000, 10000), policy) == 12
    assert crowd_morale(HomeCrowd(50000, 50000), policy) == 12
    for capacity, fans in ((-1, 0), (5000, 5001), (True, 0), (10, True)):
        with pytest.raises(ValueError):
            HomeCrowd(capacity, fans)


def test_support_is_temporary_applies_to_keeper_and_is_capped_at_maximum_morale() -> None:
    initial = demo_match()
    neutral = start_match(
        initial.match_id, initial.seed, initial.home.snapshot, initial.away.snapshot
    )
    assert initial.home.support_morale_points == 3
    assert neutral.home.support_morale_points == initial.away.support_morale_points == 0
    assert sector_ratings(initial.home).goalkeeper > sector_ratings(neutral.home).goalkeeper
    assert sector_ratings(initial.home).midfield > sector_ratings(neutral.home).midfield
    assert sector_ratings(initial.away) == sector_ratings(neutral.away)
    player = replace(initial.home.snapshot.players[0], morale=100)
    condition = initial.home.conditions[0]
    assert effective_skill(player, condition, "GK", "goalkeeping", 12) == effective_skill(
        player, condition, "GK", "goalkeeping", 0
    )
    end, _ = simulate_match(initial)
    assert end.home.snapshot == initial.home.snapshot
    assert all(p.morale == 50 for p in end.home.snapshot.players)
    doc = state_document(initial)
    doc["crowd"]["home_fans"] = 0
    with pytest.raises(ValueError):
        restore_state(doc)


def test_checkpoint_rejects_unknown_versions_fields_and_inconsistent_score() -> None:
    state = demo_match()
    for change in ("version", "field", "score", "energy"):
        doc = state_document(state)
        if change == "version":
            doc["engine_version"] = "regulation-v999"
        elif change == "field":
            doc["telegram_token"] = "not-a-token"
        elif change == "score":
            doc["home"]["stats"]["goals"] = 1
        else:
            doc["home"]["conditions"][0]["energy_bps"] = 12000
        with pytest.raises(ValueError):
            restore_state(doc)


def test_balanced_series_is_varied_and_stronger_players_have_an_advantage() -> None:
    initial = demo_match(0)
    goals = []
    winners = Counter()
    for seed in range(120):
        end, _ = simulate_match(replace(initial, seed=seed))
        h, a = end.home.stats.goals, end.away.stats.goals
        goals.append(h + a)
        winners["home" if h > a else "away" if a > h else "draw"] += 1
    assert 1.2 < sum(goals) / len(goals) < 3.5
    assert len(set(goals)) > 4 and len(winners) == 3
    assert 0 < winners["home"] - winners["away"] < 40
    strong = replace(
        initial.home,
        snapshot=replace(
            initial.home.snapshot,
            players=tuple(
                replace(p, skills=MatchSkills(*([18] * 8))) for p in initial.home.snapshot.players
            ),
        ),
    )
    weak = replace(
        initial.away,
        snapshot=replace(
            initial.away.snapshot,
            players=tuple(
                replace(p, skills=MatchSkills(*([7] * 8))) for p in initial.away.snapshot.players
            ),
        ),
    )
    wins = 0
    for seed in range(30):
        end, _ = simulate_match(replace(initial, home=strong, away=weak, seed=seed))
        wins += end.home.stats.goals > end.away.stats.goals
    assert wins >= 24
