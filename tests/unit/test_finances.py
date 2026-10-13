"""Economy policy bounds, roster costs and actual RU/EN financial presentation."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goaletto.application.finance_seed import week_start
from goaletto.application.finances import FinanceScreen
from goaletto.bot.finance_views import finance_view, parse_page
from goaletto.domain.finances import (
    INITIAL_CAPITAL,
    accrued_wage,
    money,
    player_wage,
    sponsor_income,
    ticket_income,
)
from goaletto.domain.world import WorldOptions, build_world
from goaletto.infrastructure.models import FinanceCycle, LedgerEntry
from goaletto.presentation.i18n import using_locale
from tests.unit.test_language import assert_english


def test_starter_wages_and_guaranteed_income_cover_head_coach_without_tickets():
    world = build_world(
        WorldOptions(
            key="finance-test", seed=0, first_kickoff=datetime(2026, 10, 17, 18, tzinfo=UTC)
        )
    )
    for league in world.leagues:
        for club in league.clubs:
            wages = sum(player_wage(p.skills[:8]) for p in club.players)
            assert wages == 16270
            assert sponsor_income(club.tier, 10) + 2500 > wages + 50000 + 3000
    assert INITIAL_CAPITAL >= (400000 + 2 * (16270 + 1000 + 50000 + 4000)) * 110 // 100
    assert accrued_wage(50000, 604800) == 50000
    assert accrued_wage(50000, 302400) == 25000
    assert accrued_wage(50000, 1) == 1
    assert ticket_income(2500, 5000) == 12500
    for bad in (-1, True, 2**63):
        with pytest.raises(ValueError):
            money(bad)
    with pytest.raises(ValueError):
        ticket_income(5001, 5000)
    with pytest.raises(ValueError):
        sponsor_income(0, 1)
    with pytest.raises(ValueError):
        week_start(datetime(2026, 10, 13))


def test_finance_view_english_scoped_navigation_and_maximum_ledger_fit():
    now = datetime(2026, 10, 19, tzinfo=UTC)
    owner = uuid4()
    entries = tuple(
        LedgerEntry(
            id=uuid4(),
            club_id=uuid4(),
            kind="reserve",
            cash_delta=-(2**63 - 1),
            reserved_delta=2**63 - 1,
            arrears_delta=2**63 - 1,
            occurred_at=now,
        )
        for _ in range(10)
    )
    cycle = FinanceCycle(
        id=uuid4(),
        week_start=now,
        snapshot={
            "amounts": {
                "sponsor": 80000,
                "merchandise": 2500,
                "player_salary": 16270,
                "staff_salary": 50000,
                "stadium_upkeep": 3000,
            }
        },
    )
    screen = FinanceScreen(
        "Meridian <Club>",
        owner,
        2**63 - 1,
        2**63 - 1,
        2**63 - 1,
        16270,
        50000,
        80000,
        2500,
        3000,
        now,
        cycle,
        entries,
        1,
        3,
    )
    with using_locale("en"):
        assert_english(finance_view(screen))
        text, keyboard = finance_view(screen)
        assert "&lt;Club&gt;" in text and "arrears" in text.lower()
        for row in keyboard.inline_keyboard:
            for button in row:
                if button.callback_data.startswith("fi:"):
                    assert parse_page(button.callback_data)[0] == owner
        assert_english(finance_view(replace(screen, arrears=0, last_cycle=None, page=0, pages=1)))
    for value in ("fi:open", "fi:bad:0", "fi:bad:-1", "fi:bad:１２", "fi:bad:999999999"):
        with pytest.raises(ValueError):
            parse_page(value)
