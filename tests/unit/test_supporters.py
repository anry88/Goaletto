"""Sporting success builds an audience; event demand is separate from seats."""

from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.domain.neutral_final import (
    CURRENT_NEUTRAL_POLICY,
    NEUTRAL_FINAL_POLICY,
    NeutralFinalSnapshot,
    result_document,
    settle_neutral_final,
)
from goaletto.domain.stadium import match_attendance
from goaletto.domain.supporters import match_gain, season_gain


def test_attendance_tracks_fans_tier_results_status_and_knockout_stage():
    def crowd(competition="league", stage=None, **kw):
        params = dict(supporters=2500, capacity=50000, tier=5, place=5, recent_points=())
        return match_attendance(**(params | kw), competition=competition, stage=stage).fans

    ordinary = crowd()
    assert (
        crowd("friendly")
        < ordinary
        < crowd("cup", "quarter")
        < crowd("cup", "semi")
        < crowd("cup", "final")
    )
    assert crowd("champions", "final") > crowd("cup", "final")
    assert crowd(tier=1) > ordinary and crowd(supporters=5000) > ordinary
    assert crowd(recent_points=(3,) * 5) > ordinary > crowd(recent_points=(0,) * 5)
    assert crowd(capacity=100000) == ordinary
    assert crowd("cup", "final", capacity=2000) == 2000
    assert crowd(supporters=0) == 0
    with pytest.raises(ValueError):
        crowd("cup", "invented")


def test_growth_rewards_results_upsets_qualification_and_season_achievements():
    def gain(**kw):
        params = dict(
            supporters=2500,
            tier=5,
            opponent_tier=5,
            competition="league",
            stage="league",
            goals_for=2,
            goals_against=0,
        )
        return match_gain(**(params | kw))

    win = gain()
    assert 0 < win < 250
    assert gain(opponent_tier=1) > win
    assert gain(goals_for=0, goals_against=2) == 0
    assert gain(goals_for=1, goals_against=1) == 0
    assert gain(opponent_tier=1, goals_for=1, goals_against=1) > 0
    assert gain(competition="friendly", stage="friendly") == 0
    qualification = gain(
        competition="cup", stage="quarter", goals_for=0, goals_against=0, advanced=True
    )
    assert qualification > 0
    assert gain(competition="cup", stage="final", advanced=True) > gain(
        competition="cup", stage="semi", advanced=True
    )
    assert season_gain(2500, 5, 1, 4) > season_gain(2500, 5, 2, 4) > season_gain(2500, 5, 3, 5) > 0
    assert season_gain(2500, 5, 8, 5) == 0
    with pytest.raises(ValueError):
        gain(advanced=True)


def test_v2_neutral_demand_is_frozen_both_fans_split_and_legacy_shape_survives():
    snapshot = NeutralFinalSnapshot(
        NEUTRAL_FINAL_POLICY, UUID(int=3), 50000, 5, UUID(int=1), UUID(int=2), 2500, 3000, 7, True
    )
    old = settle_neutral_final(snapshot)
    current = settle_neutral_final(
        replace(snapshot, policy_version=CURRENT_NEUTRAL_POLICY, demand_bps=22000)
    )
    assert old.crowd == 4400 < current.crowd == 12100
    assert "demand_bps" not in result_document(old)
    assert result_document(current)["demand_bps"] == 22000
    assert (
        current.payout_a + current.payout_b == current.gross
        and abs(current.payout_a - current.payout_b) <= 1
    )
    assert current.home_advantage == 0
    swapped = settle_neutral_final(
        replace(
            snapshot,
            policy_version=CURRENT_NEUTRAL_POLICY,
            demand_bps=22000,
            club_a_id=snapshot.club_b_id,
            club_b_id=snapshot.club_a_id,
            supporters_a=3000,
            supporters_b=2500,
        )
    )
    assert (swapped.crowd, swapped.gross) == (current.crowd, current.gross)
    with pytest.raises(ValueError):
        replace(snapshot, demand_bps=22000)


def test_successful_club_grows_organic_demand_without_free_friendly_growth():
    fans = 2500
    for _match in range(18):
        fans += match_gain(fans, 5, 5, "league", "league", 2, 0)
    fans += season_gain(fans, 5, 1, 4)
    assert 5000 < fans < 10000
    assert match_attendance(fans, 10000, 4, 1, (3,) * 5, competition="league").fans > 5000
    assert match_attendance(2500, 10000, 5, 10, (0,) * 5, competition="league").fans < 2500
