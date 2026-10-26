"""New injury variation, stable retries, legacy terms and concise player cards."""

from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from goaletto.bot.club_cards import player_view
from goaletto.domain.injuries import LEGACY_POLICY, match_injury, unavailable_until
from goaletto.presentation.i18n import using_locale
from tests.unit.test_club_cards import player


def test_match_injuries_vary_by_person_with_short_injuries_most_common():
    match = UUID(int=10001)
    outcomes = [match_injury(match, 42, UUID(int=i)) for i in range(10000)]
    counts = Counter(i.severity for i in outcomes)
    assert 6200 < counts["light"] < 6800
    assert 2700 < counts["moderate"] < 3300
    assert 350 < counts["severe"] < 650
    assert {i.days for i in outcomes} == set(range(1, 15))
    for injury in outcomes:
        low, high = {"light": (1, 3), "moderate": (4, 7), "severe": (8, 14)}[injury.severity]
        assert low <= injury.days <= high
    assert outcomes == [match_injury(match, 42, UUID(int=i)) for i in range(10000)]
    assert outcomes != [match_injury(match, 43, UUID(int=i)) for i in range(10000)]


def test_legacy_match_terms_keep_seven_days_without_inventing_a_severity():
    assert {
        (i.days, i.severity)
        for i in (
            match_injury(UUID(int=1), seed, UUID(int=2), policy=LEGACY_POLICY) for seed in range(50)
        )
    } == {(7, None)}
    with pytest.raises(ValueError, match="policy"):
        match_injury(UUID(int=1), 1, UUID(int=2), policy="unknown")
    with pytest.raises(ValueError):
        unavailable_until(datetime(2026, 10, 26, tzinfo=UTC), 0)


@pytest.mark.parametrize(
    ("severity", "ru", "en"),
    [
        ("light", "Лёгкая травма", "Minor injury"),
        ("moderate", "Травма средней тяжести", "Moderate injury"),
        ("severe", "Тяжёлая травма", "Severe injury"),
    ],
)
def test_player_card_shows_only_severity_and_deadline(severity, ru, en):
    card = replace(
        player(),
        injured=True,
        injured_until=datetime(2026, 11, 1, 18, 33, tzinfo=UTC),
        injury_severity=severity,
    )
    body, _ = player_view(card)
    assert ru in body and "01.11 18:33 UTC" in body
    assert body.count("🤕") == 1 and "65%" not in body and "policy" not in body
    with using_locale("en"):
        body, _ = player_view(card)
    assert en in body and ru not in body
