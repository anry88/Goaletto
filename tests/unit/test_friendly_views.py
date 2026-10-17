"""Callback budgets and rendering for friendly challenges (no database)."""

from datetime import date
from uuid import UUID, uuid4

import pytest

from goaletto.application.friendly import (
    FriendlyInviteCard,
    FriendlyOpponent,
    FriendlyScreen,
)
from goaletto.bot.friendly_views import (
    challenge_dates_view,
    challenge_opponents_view,
    friendly_view,
    parse_day,
    parse_invite,
    parse_opponent,
)
from goaletto.bot.result_views import compact_id

DAY = date(2026, 10, 20)
CLUB = UUID("12345678-1234-5678-1234-567812345678")
INVITE = uuid4()


def _card(status: str = "pending") -> FriendlyInviteCard:
    return FriendlyInviteCard(INVITE, CLUB, uuid4(), "Home", "Away", DAY, status)


def test_callback_round_trip_and_64_byte_budget() -> None:
    assert parse_day("fr:d:2026-10-20") == DAY
    assert parse_opponent(f"fr:o:2026-10-20:{CLUB}") == (DAY, CLUB)
    assert parse_invite(f"fr:a:{compact_id(INVITE)}", "a") == INVITE
    longest = [
        f"fr:o:2026-10-20:{UUID(int=(1 << 128) - 1)}",
        f"fr:a:{compact_id(INVITE)}",
        f"fr:r:{compact_id(INVITE)}",
        f"fr:x:{compact_id(INVITE)}",
    ]
    assert all(len(item.encode()) <= 64 for item in longest)
    for bad in ("fr:d:20.10.2026", "fr:o:2026-10-20", "fr:a:not-an-id", "fr:z:1"):
        with pytest.raises(ValueError):
            if bad.startswith("fr:d:"):
                parse_day(bad)
            elif bad.startswith("fr:o:"):
                parse_opponent(bad)
            else:
                parse_invite(bad, "a")


def test_friendly_screen_sections_and_buttons() -> None:
    incoming = _card()
    outgoing = FriendlyInviteCard(uuid4(), uuid4(), CLUB, "Mine", "Theirs", DAY, "pending")
    recent = FriendlyInviteCard(uuid4(), uuid4(), CLUB, "A", "B", DAY, "accepted")
    screen = FriendlyScreen(uuid4(), CLUB, "Mine", (incoming,), (outgoing,), (recent,))
    text, keyboard = friendly_view(screen, notice="Готово.")
    assert "Вас вызывают" in text and "Ваши вызовы" in text and "Недавние" in text
    assert "Готово." in text
    payloads = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert f"fr:a:{compact_id(INVITE)}" in payloads
    assert "fr:c" in payloads


def test_challenge_pickers_mark_busy_without_buttons() -> None:
    text, keyboard = challenge_dates_view((DAY,))
    assert "Свободный день" in text
    assert keyboard.inline_keyboard[0][0].text == "20.10"
    assert keyboard.inline_keyboard[0][0].callback_data == "fr:d:2026-10-20"
    opponents = (
        FriendlyOpponent(CLUB, "Human", True, False),
        FriendlyOpponent(uuid4(), "Bot", False, False),
        FriendlyOpponent(uuid4(), "Busy", True, True),
    )
    body, keys = challenge_opponents_view(DAY, opponents)
    assert "занят в этот день" in body
    opponent_payloads = [button.callback_data for row in keys.inline_keyboard for button in row]
    assert sum((item or "").startswith("fr:o:") for item in opponent_payloads) == 2
