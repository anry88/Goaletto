"""User-facing contracts for the three club-management improvements."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from goaletto.application.friendly import FriendlyOpponent
from goaletto.application.training import preferred_training_time
from goaletto.bot.club_cards import menu_view, player_view
from goaletto.bot.friendly_views import challenge_opponents_view
from goaletto.infrastructure.models import Manager
from goaletto.presentation.i18n import using_locale
from tests.unit.test_club_cards import player


@pytest.mark.parametrize(
    "zone,minute",
    [
        ("Etc/GMT-14", 60),
        ("Etc/GMT+12", 1380),
        ("Asia/Kathmandu", 7 * 60 + 30),
        ("Europe/Belgrade", 20 * 60 + 30),
        ("UTC", 0),
        ("UTC", 1439),
    ],
)
def test_training_time_keeps_utc_date_and_local_preference(zone, minute):
    day = datetime(2026, 10, 25, tzinfo=UTC)
    manager = Manager(timezone_name=zone, preferred_kickoff_minute=minute)
    due = preferred_training_time(day, manager)
    local = due.astimezone(ZoneInfo(zone))
    assert due.date() == day.date()
    assert local.hour * 60 + local.minute == minute
    assert preferred_training_time(day, None) == day.replace(hour=18)


@pytest.mark.parametrize("lang", ["ru", "en"])
def test_player_actions_menu_search_are_localized_and_bounded(lang):
    card = replace(player(), ownership_id=uuid4())
    with using_locale(lang):
        text, keys = player_view(card, 3, portrait_ready=True)
        menu = menu_view()[1]
        body, results = challenge_opponents_view(
            datetime(2026, 10, 26).date(),
            tuple(FriendlyOpponent(uuid4(), f"Club {i}", i % 2 == 0, False) for i in range(15)),
        )
    payloads = [b.callback_data for row in keys.inline_keyboard for b in row]
    assert any(x.startswith("mk:q:") for x in payloads)
    assert any(x.startswith("pr:q:") for x in payloads)
    assert "🤖" not in body and "👤" not in body
    assert (
        len(
            [
                b
                for row in results.inline_keyboard
                for b in row
                if b.callback_data.startswith("fr:o:")
            ]
        )
        == 10
    )
    assert any(b.callback_data == "fr:open" for row in menu.inline_keyboard for b in row)
    for status in ("retired", "last_season"):
        with using_locale(lang):
            _, closed = player_view(replace(card, status=status), 1, portrait_ready=True)
        assert not any(
            (b.callback_data or "").startswith(("mk:q:", "pr:q:"))
            for row in closed.inline_keyboard
            for b in row
        )
    for keyboard in (keys, results, menu):
        assert all(
            len(b.callback_data.encode()) <= 64 for row in keyboard.inline_keyboard for b in row
        )
    if lang == "en":
        assert "Расторг" not in text and "Уточните" not in body
