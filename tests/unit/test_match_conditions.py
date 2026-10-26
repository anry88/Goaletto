"""Comparable live substitute labels and deterministic injury deadlines."""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from goaletto.application.lineup import SquadMember
from goaletto.application.live_commands import LiveScreen
from goaletto.application.match_simulation import demo_match
from goaletto.bot.lineup_views import condition_label, replacement_view
from goaletto.bot.live_views import render
from goaletto.domain.injuries import unavailable_until
from goaletto.domain.match_types import Side
from goaletto.presentation.i18n import using_locale


def test_match_injury_is_seven_elapsed_days_with_utc_inputs():
    at = datetime(2026, 10, 25, 18, 33, tzinfo=UTC)
    assert unavailable_until(at) == datetime(2026, 11, 1, 18, 33, tzinfo=UTC)
    with pytest.raises(ValueError):
        unavailable_until(at.replace(tzinfo=None))


def test_both_live_players_show_form_and_energy_from_the_same_match():
    state = demo_match(7)
    team = state.home
    outgoing = team.lineup[0].player_id
    incoming = team.bench[0]
    players = tuple(
        replace(p, form=3 if p.id == outgoing else 8 if p.id == incoming else p.form)
        for p in team.snapshot.players
    )
    conditions = tuple(
        replace(
            c,
            energy_bps=5100
            if c.player_id == outgoing
            else 9200
            if c.player_id == incoming
            else c.energy_bps,
        )
        for c in team.conditions
    )
    state = replace(
        state,
        home=replace(team, snapshot=replace(team.snapshot, players=players), conditions=conditions),
    )
    screen = LiveScreen(
        uuid4(),
        team.snapshot.club_id,
        "Club",
        uuid4(),
        state.match_id,
        state,
        state,
        Side.HOME,
        1,
        False,
        datetime(2026, 10, 25, 18, tzinfo=UTC),
        True,
        (),
    )
    selected = next(i for i, p in enumerate(players, 1) if p.id == outgoing)
    _, keys = render(screen, panel=1)
    assert any(
        "форма 3/10" in b.text and "⚡ 51%" in b.text for r in keys.inline_keyboard for b in r
    )
    text, keys = render(screen, panel=5, selected=selected)
    assert "форма 3/10" in text and "⚡ 51%" in text
    assert any(
        "форма 8/10" in b.text and "⚡ 92%" in b.text for r in keys.inline_keyboard for b in r
    )
    with using_locale("en"):
        text, keys = render(screen, panel=5, selected=selected)
    assert "form 3/10" in text and "форма" not in text


def test_prematch_comparison_has_both_values_and_escapes_the_selected_name():
    out = SquadMember(uuid4(), "Ali <Out>", "MC", (), "MC", False, False, 6100, form=4)
    other = replace(out, person_id=uuid4(), name="Bob In", energy_bps=9700, form=8)
    view = replacement_view(SimpleNamespace(starters=(out,), bench=(other,)), 1)
    assert "&lt;Out&gt;" in view and "форма 4/10" in view and "⚡ 61%" in view
    assert "форма 8/10" in condition_label(other) and "⚡ 97%" in condition_label(other)
