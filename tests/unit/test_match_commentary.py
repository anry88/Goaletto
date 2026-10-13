"""Visible football incidents, committed effects and bounded safe Telegram text."""

import re
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goaletto.application import outbox_delivery
from goaletto.application.live_commands import CommandReceipt, LiveScreen
from goaletto.application.match_commentary import commentary_html, football_line, recent_commentary
from goaletto.application.match_simulation import demo_match
from goaletto.bot.live_views import render
from goaletto.domain.match_types import Side


def sample():
    return replace(demo_match(), tick=10, game_minute=30)


def event(state, kind, minute=12, detail=None, **extra):
    return dict(
        id=str(uuid4()),
        kind=kind,
        minute=minute,
        side="home",
        player_id=str(state.home.snapshot.players[0].id),
        other_player_id=str(state.home.snapshot.players[1].id),
        detail=detail,
        **extra,
    )


@pytest.mark.parametrize(
    "kind,detail,label",
    [
        ("goal", None, "Гол!"),
        ("save", None, "вратарь спасает"),
        ("wide", None, "бьёт мимо"),
        ("post", None, "штангу"),
        ("corner", None, "Угловой"),
        ("yellow", None, "Жёлтая"),
        ("red", "second_yellow", "вторая жёлтая"),
        ("injury", None, "Травма"),
        ("substitution", None, "Замена"),
        ("automatic_substitution", "injury", "из-за травмы"),
        ("tactic", "pressing", "прессинг"),
        ("mentality", "attack", "настрой — атака"),
        ("instruction", "long_shots", "бить издалека"),
    ],
)
def test_football_incidents_use_frozen_names_and_no_internal_steps(kind, detail, label):
    state = sample()
    text = football_line(state, event(state, kind, detail=detail))
    assert text.startswith("12′ ") and label in text
    assert not re.search(r"\bтик(?:и|а|ов)?\b", text.lower()) and "этап" not in text.lower()


def test_future_events_and_uninteresting_interceptions_are_not_shown():
    state = sample()
    assert football_line(state, event(state, "goal", minute=31)) is None
    assert football_line(state, event(state, "interception")) is None


def test_important_incident_survives_many_chances_and_duplicate_documents():
    state = sample()
    goal = event(state, "goal", minute=1)
    events = [goal, *[event(state, "save", minute=i) for i in range(2, 29)], goal]
    result = recent_commentary(state, events)
    assert len(result) == 5 and result[0].startswith("1′ ")
    assert sum("Гол!" in line for line in result) == 1
    assert result[-1].startswith("28′ ")


def test_html_budget_never_splits_entities_and_keeps_latest_events():
    lines = tuple(f"{i}′ {'<&>' * 40}" for i in range(12))
    rendered = commentary_html(lines)
    assert len(rendered) <= 1800 and "11′" in rendered
    assert "<" not in rendered and "&lt;&amp;&gt;" in rendered


def test_live_screen_shows_football_and_simple_command_status():
    state = sample()
    screen = LiveScreen(
        uuid4(),
        state.home.snapshot.club_id,
        "Club",
        uuid4(),
        state.match_id,
        state,
        state,
        Side.HOME,
        11,
        False,
        datetime.now(UTC),
        True,
        (CommandReceipt(uuid4(), "instruction", "accepted", 11),),
        ("12′ Гол! <Игрок>",),
    )
    text, _ = render(screen)
    assert "Гол! &lt;Игрок&gt;" in text and "Инструкция: Принято" in text
    assert (
        not re.search(r"\bтик(?:и|а|ов)?\b", text.lower())
        and "этап" not in text.lower()
        and "Дедлайн" not in text
    )


def test_sender_backwards_compatibility_and_html_safe_commentary():
    payload = dict(
        version=1,
        kind="match_board",
        fixture_id=str(uuid4()),
        ownership_id=str(uuid4()),
        match_id=str(uuid4()),
        tick=10,
        phase="first_half",
        home="Home",
        away="Away",
        home_goals=1,
        away_goals=0,
    )
    assert outbox_delivery.valid_payload(payload)
    assert "30′" in outbox_delivery.render_text(payload)
    payload.update(game_minute=29, commentary=["29′ Гол! <Игрок>"], shots=[2, 1], on_target=[1, 0])
    text = outbox_delivery.render_text(payload)
    assert "29′" in text and "&lt;Игрок&gt;" in text and "Удары: 2 / 1" in text
    payload["commentary"] = ["x"] * 13
    assert not outbox_delivery.valid_payload(payload)


def test_terminal_match_keeps_score_and_working_report_button():
    from goaletto.bot.history_callbacks import parse_history_callback
    from goaletto.domain.clock import MatchPhase

    state = replace(sample(), tick=30, game_minute=90, phase=MatchPhase.FULL_TIME)
    fixture = uuid4()
    screen = LiveScreen(
        uuid4(),
        state.home.snapshot.club_id,
        "Club",
        fixture,
        state.match_id,
        state,
        state,
        Side.HOME,
        None,
        False,
        None,
        False,
        (),
    )
    text, keyboard = render(screen)
    assert "Матч завершён" in text and "Сейчас действие недоступно" not in text
    report = next(b for row in keyboard.inline_keyboard for b in row if "Отчёт" in b.text)
    assert parse_history_callback(report.callback_data) == ("match", fixture, 1)
