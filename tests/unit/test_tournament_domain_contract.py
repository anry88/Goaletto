"""Exercise the accepted allocation -> cup -> neutral settlement API boundary."""

from uuid import uuid5

import pytest

from goaletto.domain.cup_bracket import (
    CupEntry,
    all_matches,
    build_bracket,
    champion,
    final_tie,
    resolve_slot,
)
from goaletto.domain.league_allocation import allocate
from goaletto.domain.neutral_final import (
    NEUTRAL_FINAL_POLICY,
    NeutralFinalSnapshot,
    settle_neutral_final,
)
from tests.unit.test_league_allocation import make_cohort


@pytest.mark.parametrize("sets", [1, 2, 3, 4])
@pytest.mark.parametrize("seed", [0, 7])
def test_next_membership_supports_full_cups_and_conserved_final_income(sets, seed):
    snapshots = make_cohort(sets)
    destinations = allocate(snapshots, seed=seed).by_league()
    all_next_clubs = set()
    for set_id in {row.league_set_id for row in snapshots}:
        # This fixture explicitly freezes its initial entry order; it does not
        # pretend the transferred clubs have played a past season together.
        entries = tuple(
            CupEntry(row.league_id, row.tier, destinations[row.league_id])
            for row in snapshots
            if row.league_set_id == set_id
        )
        season_id = uuid5(set_id, "upcoming-season")
        bracket = build_bracket(
            season_id=season_id, league_set_id=set_id, entries=entries, seed=seed
        )
        club_ids = {club for entry in entries for club in entry.order}
        assert len(club_ids) == 50 and not club_ids & all_next_clubs
        all_next_clubs |= club_ids
        winners = {}
        for tie in bracket.ties:
            home = resolve_slot(bracket, tie.home, winners)
            away = resolve_slot(bracket, tie.away, winners)
            assert home and away and home != away
            winners[tie.id] = min((home, away), key=lambda club: club.hex)
        assert champion(bracket, winners) in club_ids
        played = set()
        for match in all_matches(bracket):
            for slot in (match.home, match.away):
                club = resolve_slot(bracket, slot, winners)
                assert (match.cup_day, club) not in played
                played.add((match.cup_day, club))
        final = final_tie(bracket)
        home = resolve_slot(bracket, final.home, winners)
        away = resolve_slot(bracket, final.away, winners)
        result = settle_neutral_final(
            NeutralFinalSnapshot(
                NEUTRAL_FINAL_POLICY,
                uuid5(set_id, "neutral-arena"),
                10001,
                5,
                home,
                away,
                8000,
                8000,
                seed,
                final.neutral,
            )
        )
        assert result.home_advantage == 0 and result.neutral
        assert result.crowd == 10001 and result.gross == 50005
        assert result.payout_a + result.payout_b == result.gross
        assert abs(result.payout_a - result.payout_b) == 1
    assert all_next_clubs == {club for row in snapshots for club in row.order}
