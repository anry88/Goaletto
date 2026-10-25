"""Youth decisions must keep hidden potential and season/price callback scope."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goaletto.application.academy import AcademyScreen, CandidateView
from goaletto.bot.academy_views import academy_view, parse_academy
from goaletto.bot.result_views import compact_id


def test_callbacks_pin_season_and_price_without_revealing_caps():
    owner, season = uuid4(), uuid4()
    screen = AcademyScreen(
        club_name="Club",
        ownership_id=owner,
        level=1,
        season_id=season,
        available=600000,
        arrears=0,
        upkeep=6000,
        next_upgrade_cost=200000,
        sign_limit=1,
        signed=0,
        roster_size=20,
        candidates=(
            CandidateView(
                1, "Name", 17, "GK", (), {"goalkeeping": 5}, {"goalkeeping": 19}, "available"
            ),
        ),
    )
    text, keyboard = academy_view(screen)
    assert "19" not in text
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    signing = next(c for c in callbacks if c.startswith("ac:g:"))
    upgrade = next(c for c in callbacks if c.startswith("ac:u:"))
    assert parse_academy(signing) == ("g", owner, 1, season)
    assert parse_academy(upgrade) == ("u", owner, 1, None)
    assert all(len(c.encode()) <= 64 for c in callbacks)
    _, closed = academy_view(replace(screen, intake_open=False))
    assert not any(
        (b.callback_data or "").startswith("ac:g:") for row in closed.inline_keyboard for b in row
    )
    for legacy in (f"ac:g:{compact_id(owner)}:1", f"ac:u:{compact_id(owner)}"):
        with pytest.raises(ValueError):
            parse_academy(legacy)
    prospect = replace(
        screen,
        intake_open=False,
        signing_opens_at=datetime(2027, 1, 1, tzinfo=UTC),
        candidates=tuple(replace(view, status="prospect") for view in screen.candidates),
    )
    text, closed = academy_view(prospect)
    assert "Перспективные игроки академии" in text
    assert "01.01.2027" in text and "после окончания сезона" in text
    assert "следующем межсезонье можно подписать: 1" in text
    assert "подписано в этом сезоне" not in text
    assert not any(
        (b.callback_data or "").startswith("ac:g:") for row in closed.inline_keyboard for b in row
    )
    from goaletto.presentation.i18n import using_locale

    with using_locale("en"):
        english, _ = academy_view(prospect)
    assert "Academy prospects" in english and "after the season ends" in english
