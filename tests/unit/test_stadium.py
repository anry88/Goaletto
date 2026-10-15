"""Demand, capacity, interval bills and priced RU/EN stadium presentation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.stadium import StadiumScreen
from goaletto.bot.stadium_views import parse_stadium, stadium_view
from goaletto.domain.match_types import HomeCrowd
from goaletto.domain.stadium import attendance, period_upkeep
from goaletto.infrastructure.models import StadiumExpansion
from goaletto.presentation.i18n import using_locale
from tests.unit.test_language import assert_english


def test_attendance_tracks_position_form_tier_and_real_fans_not_empty_seats():
    weak = attendance(2500, 5000, 5, 10, (0,) * 5)
    strong = attendance(2500, 5000, 5, 1, (3,) * 5)
    assert weak.fans == 1600 and strong.fans == 3500
    assert attendance(2500, 50000, 5, 10, (0,) * 5).fans == weak.fans
    assert attendance(2500, 50000, 1, 1, (3,) * 5).fans > strong.fans
    assert attendance(0, 50000, 1, 1, ()).fans == 0
    assert attendance(100000, 5000, 1, 1, (3,) * 5).fans == 5000
    with pytest.raises(ValueError):
        attendance(2500, 5000, 5, 1, (True,))
    with pytest.raises(ValueError):
        attendance(2500, 5000, 5, 1, (1,) * 6)


def test_upkeep_splits_only_effective_capacity_intervals():
    start = datetime(2026, 10, 19, tzinfo=UTC)
    due, intervals = period_upkeep(
        5000, start, start + timedelta(days=4), ((start + timedelta(days=2), 10000),)
    )
    assert due == 3500 and [r["capacity"] for r in intervals] == [5000, 10000]
    assert period_upkeep(5000, start, start + timedelta(days=4), ())[0] == 3000
    with pytest.raises(ValueError):
        period_upkeep(
            5000, start, start + timedelta(days=4), ((start - timedelta(seconds=1), 10000),)
        )


def test_stadium_screen_english_costs_scoped_callbacks_pending_and_pinned():
    now = datetime(2026, 10, 17, 18, tzinfo=UTC)
    owner = uuid4()
    screen = StadiumScreen(
        "Meridian",
        owner,
        5000,
        2500,
        600000,
        0,
        None,
        now,
        attendance(2500, 5000, 5, 10, (0,) * 5),
        None,
    )
    with using_locale("en"):
        view = stadium_view(screen)
        assert_english(view)
        assert "300 000" in view[0] and "4 000/club pay period" in view[0] and "1 600" in view[0]
        actions = [
            b.callback_data
            for row in view[1].inline_keyboard
            for b in row
            if b.callback_data.startswith("st:")
        ]
        assert len(actions) == 2 and parse_stadium(actions[0]) == ("u", owner, 5000)
        assert (
            len(
                [
                    b
                    for row in stadium_view(replace(screen, available=100))[1].inline_keyboard
                    for b in row
                    if b.callback_data.startswith("st:u:")
                ]
            )
            == 0
        )
        assert_english(
            stadium_view(
                replace(screen, capacity=50000, projected=None, locked=HomeCrowd(5000, 1600))
            )
        )
        expansion = StadiumExpansion(to_capacity=10000, cost=300000, due_at=now + timedelta(days=4))
        assert_english(stadium_view(replace(screen, expansion=expansion)))
    for bad in ("st:open", "st:u:bad:5000", "st:u:bad:0", "st:u:bad:５０００", "st:u:bad:999999"):
        with pytest.raises(ValueError):
            parse_stadium(bad)
