from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goaletto.application.training import TrainingDate
from goaletto.bot.training_views import date_callback, date_description, parse_date
from goaletto.domain.game_cycle import CYCLE_SECONDS
from goaletto.domain.sponsorship import sponsor_accrual, sponsor_terms
from goaletto.presentation.i18n import using_locale


def test_season_terms_reward_result_destination_and_fans_without_a_quality_jump():
    assert sponsor_terms(5, None, 2500).cycle_rate == 51000
    assert sponsor_terms(5, 10, 2500).cycle_rate == 51000
    assert sponsor_terms(4, 1, 2500).cycle_rate == 59500
    assert sponsor_terms(5, 10, 100000).cycle_rate == sponsor_terms(5, 10, 10000).cycle_rate
    for tier in range(1, 6):
        rates = [sponsor_terms(tier, place, 2500).cycle_rate for place in range(1, 11)]
        assert all(a - b == 500 for a, b in zip(rates, rates[1:], strict=False))


@pytest.mark.parametrize(
    "args",
    [(True, 1, 2), (0, 1, 2), (6, 1, 2), (1, 11, 2), (1, False, 2), (1, None, -1), (1, None, True)],
)
def test_invalid_sponsor_basis_is_rejected(args):
    with pytest.raises(ValueError):
        sponsor_terms(*args)


def test_split_invoices_and_microseconds_never_create_extra_money():
    cycle = CYCLE_SECONDS * 1_000_000
    points = [0, 1, 999999, cycle // 3, cycle - 1, cycle, cycle * 20 + 3]
    parts = [sponsor_accrual(51001, a, b) for a, b in zip(points, points[1:], strict=False)]
    assert sum(parts) == sponsor_accrual(51001, points[0], points[-1]) == 51001 * 20
    assert sponsor_accrual(51000, 0, cycle // 2) == 25500
    with pytest.raises(ValueError):
        sponsor_accrual(51000, 2, 1)


def test_date_callbacks_and_conditional_day_explain_the_reservation_in_both_locales():
    owner, day = uuid4(), datetime(2026, 10, 15, tzinfo=UTC)
    data = date_callback(owner, day)
    assert len(data.encode()) <= 64 and parse_date(data) == (owner, day)
    for invalid in (data + ":extra", data[:-8] + "20260230", data[:-8] + "２０２６１０１５"):
        with pytest.raises(ValueError):
            parse_date(invalid)
    date = TrainingDate(day, "conditional", "Hidden opponent", "champions")
    for locale, expected in (("ru", "Возможный матч"), ("en", "Possible match")):
        with using_locale(locale):
            text = date_description(date)
            assert expected in text and "Hidden opponent" not in text
