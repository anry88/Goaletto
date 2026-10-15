"""Locale isolation, catalog coverage and multilingual gameplay presentation."""

import ast
import asyncio
import re
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Formatter
from uuid import uuid4

import pytest

from goaletto.application.history import get_match_report
from goaletto.application.lineup import load_lineup
from goaletto.application.live_commands import LiveScreen
from goaletto.application.match_commentary import football_line
from goaletto.application.match_simulation import demo_match
from goaletto.application.outbox_delivery import render_text
from goaletto.application.training import TrainingScreen
from goaletto.bot import (
    club_cards,
    history_cards,
    lineup_views,
    live_views,
    settings_views,
    training_views,
)
from goaletto.bot.player_router import caption_fits
from goaletto.domain.match_types import Side
from goaletto.domain.names import NAME_POOLS, international_catalog, latin_name
from goaletto.domain.world import WorldOptions, build_world
from goaletto.presentation.i18n import ENGLISH, locale, tr, using_locale
from tests.unit.test_club_cards import club, player, squad_page
from tests.unit.test_history import Recorder as HistoryRecorder
from tests.unit.test_history import session as history_session
from tests.unit.test_lineup import Recorder as LineupRecorder
from tests.unit.test_settings_views import enabled_state


def cyrillic(value):
    return bool(re.search(r"[А-Яа-яЁё]", value))


def assert_english(view):
    text, keyboard = view
    assert not cyrillic(text.replace("Язык", "")), text
    assert len(text.encode("utf-16-le")) // 2 < 4096
    for row in keyboard.inline_keyboard:
        for button in row:
            assert (
                button.text == "✓ Русский" or button.text == "Русский" or not cyrillic(button.text)
            )
            assert len((button.callback_data or "").encode()) <= 64


async def test_english_main_path_and_identical_callbacks():
    recorder = HistoryRecorder()
    report = await get_match_report(history_session(recorder), recorder.actor, recorder.fixture.id)
    lineup = LineupRecorder()
    data = await load_lineup(lineup, lineup.actor)
    now = datetime(2026, 10, 13, tzinfo=UTC)
    initial = demo_match()
    screen = LiveScreen(
        uuid4(),
        initial.home.snapshot.club_id,
        "<Клуб>",
        uuid4(),
        initial.match_id,
        initial,
        initial,
        Side.HOME,
        1,
        False,
        now + timedelta(seconds=60),
        True,
        (),
    )
    training = TrainingScreen("<Клуб>", uuid4(), now, now + timedelta(days=7), None, None, ())
    ru_callbacks = [
        b.callback_data for row in club_cards.menu_view()[1].inline_keyboard for b in row
    ]
    with using_locale("en"):
        for view in (
            club_cards.menu_view(),
            club_cards.club_view(club()),
            club_cards.squad_view(squad_page(1)),
            club_cards.player_view(player("last_season")),
            club_cards.player_view(player("retired")),
            history_cards.report_view(report),
            history_cards.report_view(report, section="home"),
            live_views.render(screen),
            training_views.training_view(training),
            settings_views.settings_view(replace(enabled_state(), locale="en")),
        ):
            assert_english(view)
        assert not cyrillic(lineup_views.render_main(data))
        assert_english(("", lineup_views.tactic_keyboard(data)))
        assert_english(("", lineup_views.mentality_keyboard(data)))
        assert caption_fits(club_cards.player_view(player())[0])
        en_callbacks = [
            b.callback_data for row in club_cards.menu_view()[1].inline_keyboard for b in row
        ]
        assert en_callbacks == ru_callbacks
        assert tr("Клуб: {p0}", p0="Текст & <name>") == "Club: Текст & <name>"  # data stays opaque
    assert locale() == "ru"


def test_names_are_unique_diverse_and_cosmetic_only():
    catalog = international_catalog(0)
    counts = Counter(tradition for tradition, _ in catalog)
    assert set(counts) == {t for t, _, _ in NAME_POOLS} | {"vietnamese"}
    assert set(counts.values()) <= {37, 38}
    assert len({name for _, name in catalog}) == 1000
    assert catalog == international_catalog(0) and catalog != international_catalog(1)
    assert all(not cyrillic(name) for _, name in catalog)
    assert any("Đ" in name or "Ł" in name for _, name in catalog)
    options = WorldOptions(
        key="names", seed=0, first_kickoff=datetime(2026, 10, 24, 18, tzinfo=UTC)
    )
    new = build_world(options)
    old = build_world(replace(options, naming_policy="legacy-v1"))
    assert {p.id for p in new.players} == {p.id for p in old.players}
    for a, b in zip(new.players, old.players, strict=True):
        assert (a.id, a.age, a.position, a.skills, a.retirement) == (
            b.id,
            b.age,
            b.position,
            b.skills,
            b.retirement,
        )
    assert [c.tie_break_order for league in new.leagues for c in league.clubs] == [
        c.tie_break_order for league in old.leagues for c in league.clubs
    ]
    assert latin_name("Семён Федотов") == "Semyon Fedotov"
    assert latin_name("José García & Nguyễn Văn An") == "José García & Nguyễn Văn An"


async def test_locale_isolated_between_concurrent_updates():
    async def read(code):
        with using_locale(code):
            await asyncio.sleep(0)
            return tr("🏟 Клуб")

    assert await asyncio.gather(read("ru"), read("en")) == ["🏟 Клуб", "🏟 Club"]
    assert locale() == "ru"


def test_all_literal_templates_covered_and_placeholders_preserved():
    for key, value in ENGLISH.items():

        def fields(s):
            return sorted(
                (field, spec, conv) for _, field, spec, conv in Formatter().parse(s) if field
            )

        assert fields(key) == fields(value), (key, value)
    sources = list(Path("src/goaletto/bot").glob("*.py")) + [
        Path("src/goaletto/application/match_commentary.py"),
        Path("src/goaletto/application/outbox_delivery.py"),
    ]
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tr"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                assert node.args[0].value in ENGLISH, (source, node.lineno)


def test_notification_uses_delivery_locale_and_localized_timeline():
    state = demo_match()
    event = {
        "kind": "save",
        "minute": 0,
        "side": "home",
        "player_id": str(state.home.snapshot.players[0].id),
    }
    timelines = {}
    for code in ("ru", "en"):
        with using_locale(code):
            timelines[code] = [football_line(state, event)]
    payload = {
        "version": 1,
        "kind": "match_result",
        "fixture_id": str(uuid4()),
        "ownership_id": str(uuid4()),
        "match_id": str(state.match_id),
        "tick": 30,
        "phase": "full_time",
        "home": "Меридиан",
        "away": "Спутник",
        "home_goals": 1,
        "away_goals": 2,
        "commentary_by_locale": timelines,
        "commentary": timelines["ru"],
    }
    with using_locale("en"):
        result = render_text(payload)
        assert "Full-time: Meridian 1:2 Sputnik" in result and "keeper saves" in result
        assert not cyrillic(result)


@pytest.fixture(autouse=True)
def full_energy_projection(monkeypatch):
    # These recording sessions test navigation/ownership; recovery has PG coverage.
    from unittest.mock import AsyncMock

    from goaletto.application.recovery import EnergyStatus

    monkeypatch.setattr(
        "goaletto.application.recovery.energy_status",
        AsyncMock(return_value=EnergyStatus(10000, None)),
    )
