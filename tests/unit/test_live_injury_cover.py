"""V9 injury cover and manual replacement after a failed automatic attempt."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from goaletto.application.live_commands import LiveScreen, button_payload
from goaletto.application.match_simulation import simulate_match
from goaletto.bot.live_views import parse_callback, render
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_engine import advance_tick, start_match, start_second_half
from goaletto.domain.match_types import CommandKind, MatchCommand, Side
from tests.unit.test_personal_morale_retirement import match


def injury_match(version, seed, healthy_positions, *, automatic=True):
    initial = match(version, 50)
    team = initial.home.snapshot
    bench = set(team.bench)
    players = tuple(
        replace(p, alternatives=(), available=p.position in healthy_positions)
        if p.id in bench
        else p
        for p in team.players
    )
    return start_match(
        initial.match_id,
        seed,
        replace(team, players=players, automatic_injury_substitutions=automatic),
        initial.away.snapshot,
        policy=replace(
            initial.policy,
            injury_bps=10000,
            yellow_bps=0,
            direct_red_bps=0,
            foul_bps=0,
            base_chance_rate_bps=0,
        ),
        crowd=initial.crowd,
        engine_version=initial.engine_version,
    )


def test_v9_uses_nearby_outfielder_when_no_exact_or_declared_cover_exists():
    # Each engine seeds its own streams. These seeds both injure the right midfielder.
    old, new = (
        advance_tick(injury_match(v, seed, {"DR", "FC", "GK"}), 1) for v, seed in ((8, 2), (9, 0))
    )
    injured = next(e.player_id for e in new.events if e.kind == "injury" and e.side == Side.HOME)
    assert any(
        s.player_id == injured and s.position == "MR" for s in new.state.home.snapshot.lineup
    )
    assert any(e.player_id == injured and e.detail == "no_healthy_reserve" for e in old.events)
    replaced = next(
        e for e in new.events if e.kind == "automatic_substitution" and e.side == Side.HOME
    )
    incoming = next(p for p in new.state.home.snapshot.players if p.id == replaced.other_player_id)
    assert replaced.player_id == injured and incoming.position == "DR"
    assert any(s.player_id == incoming.id and s.position == "MR" for s in new.state.home.lineup)
    assert len(new.state.home.lineup) == 11


def test_v9_healthy_outfielder_covers_injured_goalkeeper_when_bench_keeper_unavailable():
    # This seed injures the home goalkeeper on tick one.
    state = injury_match(9, 2, {"DR"})
    result = advance_tick(state, 1)
    cover = next(s for s in result.state.home.lineup if s.position == "GK")
    assert cover.player_id == next(
        p.id for p in state.home.snapshot.players if p.id in state.home.bench and p.position == "DR"
    )
    assert len(result.state.home.lineup) == 11
    assert not any(e.kind == "emergency_goalkeeper" and e.side == Side.HOME for e in result.events)


def test_v9_injured_player_is_selectable_and_can_be_replaced_after_checkpoint():
    result = advance_tick(injury_match(9, 0, {"DR"}, automatic=False), 1)
    state = restore_state(state_document(result.state))
    vacancy = state.home.injury_vacancies[0]
    assert vacancy.position == "MR" and vacancy.player_id in state.home.removed
    assert len(state.home.lineup) == 10
    screen = LiveScreen(
        UUID(int=111),
        state.home.snapshot.club_id,
        state.home.snapshot.name,
        UUID(int=112),
        state.match_id,
        state,
        state,
        Side.HOME,
        2,
        False,
        datetime(2026, 11, 21, tzinfo=UTC),
        True,
        (),
    )
    _, keyboard = render(screen, panel=1)
    selection = next(
        b.callback_data for row in keyboard.inline_keyboard for b in row if b.text.startswith("🤕")
    )
    action, (index,), *_ = parse_callback(selection)
    assert action == "o"
    _, keyboard = render(screen, panel=5, selected=index)
    callback = next(
        b.callback_data
        for row in keyboard.inline_keyboard
        for b in row
        if b.callback_data.startswith("lv:s:")
    )
    action, args, *_ = parse_callback(callback)
    kind, payload = button_payload(screen, action, args)
    assert kind == "substitution"
    assert payload["outgoing_id"] == str(vacancy.player_id)
    incoming = UUID(payload["incoming_id"])
    command = MatchCommand(
        UUID(int=113),
        Side.HOME,
        2,
        CommandKind.SUBSTITUTION,
        outgoing_id=vacancy.player_id,
        incoming_id=incoming,
    )
    quiet = replace(state, policy=replace(state.policy, injury_bps=0))
    applied = advance_tick(quiet, 2, (command,))
    assert applied.accepted_commands == (command.id,)
    assert not applied.state.home.injury_vacancies
    assert len(applied.state.home.lineup) == 11
    assert applied.state.home.removed.count(vacancy.player_id) == 1
    assert any(s.player_id == incoming and s.position == "MR" for s in applied.state.home.lineup)


def test_v8_checkpoint_and_match_hash_stay_unchanged():
    initial = match(8, 50)
    assert "injury_vacancies" not in state_document(initial)["home"]
    assert state_hash(initial) == "a782f3506e99423e2d67486122331031e2560b7e0a8dbc8a8bb3205c99564221"
    final, _ = simulate_match(restore_state(state_document(initial)))
    assert state_hash(final) == "eedde3e607bbf6b7b41748e31e6557eb9b8b5e074e633cff08e1ddaa850f735d"


def test_v9_can_fill_injury_vacancy_at_halftime_after_three_used_windows():
    state = advance_tick(
        injury_match(9, 0, {"DR", "DL", "DC", "MC", "FC"}, automatic=False), 1
    ).state
    state = replace(state, policy=replace(state.policy, injury_bps=0))
    healthy = {c.player_id for c in state.home.conditions if not c.injured}
    for tick in (2, 3, 4):
        command = MatchCommand(
            UUID(int=tick),
            Side.HOME,
            tick,
            CommandKind.SUBSTITUTION,
            outgoing_id=state.home.lineup[1].player_id,
            incoming_id=next(p for p in state.home.bench if p in healthy),
        )
        result = advance_tick(state, tick, (command,))
        assert result.accepted_commands == (command.id,)
        state = result.state
    injury = state.home.injury_vacancies[0]
    command = MatchCommand(
        UUID(int=16),
        Side.HOME,
        5,
        CommandKind.SUBSTITUTION,
        outgoing_id=injury.player_id,
        incoming_id=next(p for p in state.home.bench if p in healthy),
    )
    result = advance_tick(state, 5, (command,))
    assert result.rejected_commands == ((command.id, "window_limit"),)
    state = result.state
    for tick in range(6, 16):
        state = advance_tick(state, tick).state
    applied = start_second_half(state, (replace(command, target_tick=16, halftime=True),))
    assert applied.accepted_commands == (command.id,)
    assert applied.state.home.substitution_windows == (2, 3, 4)
    assert applied.state.home.substitutions == 4
    assert len(applied.state.home.lineup) == 11 and not applied.state.home.injury_vacancies
