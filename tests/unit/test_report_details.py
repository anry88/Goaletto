from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.history import ReportEvent
from goaletto.application.live_commands import CommandReceipt, LiveScreen
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.bot.history_callbacks import parse_report_callback, report_callback
from goaletto.bot.history_cards import report_view
from goaletto.bot.live_views import render
from goaletto.domain.match_types import Side
from tests.unit.test_history import Recorder, get_match_report, session


async def test_form_energy_and_substitutes_use_frozen_data() -> None:
    recorder = Recorder()
    report = await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)
    # Read energy directly from terminal conditions; do not treat minutes as fatigue.
    conditions = {str(c["player_id"]): c for c in recorder.matches[0].state["home"]["conditions"]}
    for member in report.home_lineup:
        assert member.energy_bps == conditions[str(member.person_id)]["energy_bps"]
    unused = next(m for m in report.home_lineup if not m.started)
    substitute = replace(unused, name="Вышедший <игрок>", appeared=True, minutes=0, energy_bps=9800)
    starter = replace(report.home_lineup[0], energy_bps=6100, form=5, age=31, minutes=90)
    changed = replace(report, home_lineup=(starter, substitute, unused))
    text, _ = report_view(changed, section="home")
    assert "возраст 31 · сыграл 90 мин" in text
    assert "Форма до матча: 5/8 · Энергия после: 61%" in text
    assert "Vyshedshiy &lt;igrok&gt;" in text and "Вышел на замену" in text
    assert "Не выходил" in text


async def test_report_pagination_preserves_all_players_and_actions() -> None:
    recorder = Recorder()
    report = await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)
    events = tuple(
        ReportEvent(kind, i, i * 3, "home", "<Игрок>", None, detail)
        for i, (kind, detail) in enumerate(
            [
                ("tactic", "pressing"),
                ("mentality", "attack"),
                ("instruction", "runs_behind"),
                *[("goal", None)] * 15,
            ]
        )
    )
    report = replace(report, events=tuple(reversed(events)))
    first, kb = report_view(report, 3)
    second, _ = report_view(report, 3, page=2)
    assert "Тактика: прессинг" in first and "Настрой: атака" in first
    assert "Инструкция: рывки за спину" in first
    assert "pressing" not in first and "attack" not in first and "runs_behind" not in first
    assert "0′" in first and "51′" in second
    assert "hx:r:3" in [b.callback_data for row in kb.inline_keyboard for b in row]
    for section in ("events", "home", "away"):
        page = 1
        while True:
            text, keyboard = report_view(report, 3, section=section, page=page)
            assert len(text.encode("utf-16-le")) // 2 < 4096
            buttons = [b for row in keyboard.inline_keyboard for b in row]
            for b in buttons:
                assert len(b.callback_data.encode()) <= 64
                if b.callback_data.startswith("hr:"):
                    assert parse_report_callback(b.callback_data)[0] == report.fixture_id
            if not any(b.text == "Следующая →" for b in buttons):
                break
            page += 1
    with pytest.raises(ValueError):
        report_view(report, section="home", page=99)
    for malformed in (
        "hr:bad:home:1:1",
        report_callback(report.fixture_id, "home").replace(":1:1", ":01:1"),
    ):
        with pytest.raises(ValueError):
            parse_report_callback(malformed)


def test_final_screen_has_timeline_without_anonymous_command_duplicates() -> None:
    state = demo_match(7)
    final, _ = simulate_match(state)
    now = datetime(2026, 10, 13, 12, tzinfo=UTC)
    screen = LiveScreen(
        uuid4(),
        state.home.snapshot.club_id,
        "Клуб",
        uuid4(),
        state.match_id,
        state,
        state,
        Side.HOME,
        1,
        False,
        now + timedelta(seconds=60),
        True,
        (CommandReceipt(uuid4(), "substitution", "applied", 1),),
        ("87′ Замена: Игрок → Сменщик",),
    )
    assert "Замена: Применено" not in render(screen)[0]
    final_text, _ = render(replace(screen, state=final, projected=final, open=False))
    assert "87′ Замена: Игрок → Сменщик" in final_text
    assert "Замена: Применено" not in final_text


def test_upcoming_fixture_has_timezone_lock_and_lineup_navigation() -> None:
    now = datetime(2026, 10, 13, 12, tzinfo=UTC)
    screen = LiveScreen(
        uuid4(),
        uuid4(),
        "Клуб",
        uuid4(),
        None,
        None,
        None,
        Side.HOME,
        None,
        False,
        None,
        False,
        (),
        next_fixture=("<Дом>", "Гости", now),
        next_lineup_lock_at=now - timedelta(minutes=1),
        next_is_home=True,
    )
    text, kb = render(screen)
    assert "&lt;Dom&gt; — Gosti" in text and "13.10.2026 12:00 UTC" in text
    assert "Сохраните состав до 13.10.2026 11:59 UTC" in text and "Вы играете дома" in text
    assert any(b.callback_data == "ln:open" for row in kb.inline_keyboard for b in row)
    text, _ = render(replace(screen, next_locked=True))
    assert "уже зафиксирован" in text and "Сохраните состав" not in text


async def test_practice_report_shows_skill_fraction_form_and_legacy_absence():
    from goaletto.presentation.i18n import using_locale

    recorder = Recorder()
    report = await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)
    member = replace(
        report.home_lineup[0],
        growth_bps=288,
        experience_gain=1200,
        skill_gains=(("playmaking", 288),),
        form_before=8,
        form_after=7,
    )
    changed = replace(report, home_lineup=(member, report.home_lineup[-1]))
    text, _ = report_view(changed, section="home")
    assert "Плеймейкинг: +2.88% балла" in text
    assert "опыт +12.00% балла" in text
    assert "Форма после матча: 8 → 7/8" in text
    assert text.count("Форма после матча") == 1
    # Legacy/unused players have no inferred zeroes or retrospective form changes.
    original, _ = report_view(report, section="home")
    assert "Форма после матча" not in original
    with using_locale("en"):
        english, _ = report_view(changed, section="home")
    assert "Form after match: 8 → 7/8" in english
    assert "Playmaking: +2.88%" in english


async def test_cup_report_separates_penalties_and_shows_frozen_neutral_revenue():
    from goaletto.presentation.i18n import using_locale

    recorder = Recorder()
    report = await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)
    report = replace(
        report,
        competition="cup",
        aggregate=(1, 1),
        penalties=(4, 3),
        neutral_venue=("<Арена>", 5000, 4000, 10000, 10000),
    )
    text, _ = report_view(report)
    assert "Кубок" in text and "Общий счёт: 1:1" in text and "Пенальти: 4:3" in text
    assert "4000/5000" in text and "&lt;Arena&gt;" in text and "10000" in text
    with using_locale("en"):
        text, _ = report_view(report)
    assert "Neutral stadium:" in text and "Tickets" in text and "Penalties: 4:3" in text
