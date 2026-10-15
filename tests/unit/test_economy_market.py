"""Valuation boundaries, seeded adult quality and capital/scout constraints."""

from collections import Counter
from uuid import UUID

import pytest

from goaletto.domain.finances import (
    INITIAL_CAPITAL,
    STAFF_RATES,
    player_fee,
    player_quality,
    player_wage,
    scout_mission_fee,
    scout_signing_fee,
    sponsor_income,
    staff_fee,
    staff_wage,
)
from goaletto.domain.market_players import market_player
from goaletto.domain.season_rollover import youth_profile
from goaletto.domain.training import SKILLS


def test_quality_age_positions_and_staff_experience_have_financial_cost():
    skills = (10,) * 8
    assert player_fee(skills, 26, "MC") == 300000
    assert player_fee(skills, 30, "MC") == 200000
    assert player_wage(skills, 26, "MC") == 1664
    assert player_wage(skills, 30, "MC") == 1536
    assert player_fee(skills, 26, "MC", 3) == 390000
    assert player_wage(skills, 26, "MC", 3) > player_wage(skills, 26, "MC", 2)
    for role in STAFF_RATES:
        assert staff_fee(role, 5, 50) > staff_fee(role, 5, 56)
        assert staff_wage(role, 5, 50) > staff_wage(role, 5, 56)
        assert staff_wage(role, 5, 50, loyal=True) == staff_wage(role, 5, 50) * 80 // 100
        assert all(staff_wage(role, s, 50) < staff_wage(role, s + 4, 50) for s in (1, 5, 9, 13))
    assert sponsor_income(1, 1, 10000) == sponsor_income(1, 1, 10**9)
    assert sponsor_income(4, 10) > sponsor_income(5, 10)


def test_market_varies_ages_quality_and_potential_without_changing_youth():
    profiles = [market_player(UUID(int=n), "MC") for n in range(1000)]
    assert profiles == [market_player(UUID(int=n), "MC") for n in range(1000)]
    assert {p.age for p in profiles} == set(range(26, 31))
    counts = Counter((player_quality(p.skills[:8], "MC") - 1) // 4 + 1 for p in profiles)
    assert set(counts) == set(range(1, 6))
    assert counts[5] < counts[4] < counts[3]
    assert len({tuple(p.caps.items()) for p in profiles}) > 100
    assert len({player_fee(p.skills[:8], p.age, "MC") for p in profiles}) > 40
    assert all(p.skills[0] <= 3 for p in profiles)
    for p in profiles:
        assert all(
            1 <= value <= p.caps[key] <= 20 for key, value in zip(SKILLS, p.skills[:8], strict=True)
        )
    gk = [market_player(UUID(int=n), "GK") for n in range(100)]
    assert any(p.skills[0] >= 17 for p in gk)
    _, youth = youth_profile(0, UUID(int=7), "MC")
    assert youth[2] == 12 and sum(youth[:8]) == 74


def test_scout_idleness_is_cheap_but_repeated_discoveries_are_not_a_youth_faucet():
    mission = scout_mission_fee(5, 4)
    signing = scout_signing_fee((10,) * 8, 19, "MC")
    assert staff_wage("scout", 5, 50) * 50 < mission
    assert mission + signing == 400000
    assert 3 * mission > INITIAL_CAPITAL // 2
    assert 20 * (mission + signing) > 10 * INITIAL_CAPITAL
    # Even the weakest discovery has a substantial combined price.
    assert scout_mission_fee(1, 4) + scout_signing_fee((1,) * 8, 21, "MC") > INITIAL_CAPITAL // 4
    assert scout_signing_fee((18,) * 8, 19, "MC") + mission > 1000000


@pytest.mark.parametrize(
    "call",
    [
        lambda: player_wage((True,) * 8, 27, "MC"),
        lambda: player_fee((10,) * 8, True, "MC"),
        lambda: player_fee((10,) * 8, 26, "MC", 4),
        lambda: staff_wage("scout", 0),
        lambda: staff_wage("head_coach", 1, True),
        lambda: scout_mission_fee(True, 4),
        lambda: scout_signing_fee((10,) * 8, 26, "MC"),
        lambda: player_quality((10,) * 8, "XX"),
        lambda: market_player(UUID(int=1), "XX"),
    ],
)
def test_invalid_policy_inputs_fail_closed(call):
    with pytest.raises(ValueError):
        call()


@pytest.mark.parametrize("days", [0, 1, 3, 5, 20, True, 4.0])
def test_scout_rejects_unsupported_search_duration(days):
    with pytest.raises(ValueError):
        scout_mission_fee(5, days)


def test_long_search_price_and_individual_findings_are_not_three_fixed_bands():
    assert [scout_mission_fee(5, d) for d in (4, 8, 12, 16)] == [125000, 250000, 375000, 500000]
    base = (10,) * 8
    near = (*base[:6], 11, base[7])
    assert player_quality(base, "MC") == player_quality(near, "MC") == 10
    assert scout_signing_fee(base, 19, "MC") == 275000
    assert scout_signing_fee(near, 19, "MC") == 286110
    assert len({scout_signing_fee((q,) * 8, 19, "MC") for q in range(1, 21)}) > 10
