from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from goaletto.application.discipline import tournament_scope
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.discipline import settle_cards
from goaletto.domain.match_types import EnginePolicy
from goaletto.infrastructure.models import ChampionsTie, Fixture, FixturePlan


@pytest.mark.parametrize("carried", [0, 1])
@pytest.mark.parametrize(
    "yellows,dismissed", [(0, False), (1, False), (0, True), (1, True), (2, True)]
)
def test_accumulation_and_one_ban_per_dismissal(carried, yellows, dismissed):
    change = settle_cards(
        carried, 0, yellows=yellows, dismissed=dismissed, serving=False, completed=True
    )
    assert change.awarded == int(dismissed or carried + yellows == 2)
    assert change.bans == change.awarded
    assert change.yellows == (carried if dismissed else (carried + yellows) % 2)


def test_each_second_caution_starts_a_new_pair():
    carried = bans = 0
    for index in range(6):
        change = settle_cards(
            carried, bans, yellows=1, dismissed=False, serving=False, completed=True
        )
        carried, bans = change.yellows, change.bans
        assert change.awarded == index % 2
        # Serve the ban before the next appearance.
        if bans:
            served = settle_cards(
                carried, bans, yellows=0, dismissed=False, serving=True, completed=True
            )
            bans = served.bans
            assert served.served


def test_only_completed_game_serves_ban_and_carries_prior_caution():
    aborted = settle_cards(1, 1, yellows=0, dismissed=False, serving=True, completed=False)
    assert not aborted.served and aborted.bans == 1 and aborted.yellows == 1
    served = settle_cards(1, 1, yellows=0, dismissed=False, serving=True, completed=True)
    assert served.served and served.bans == 0 and served.yellows == 1
    with pytest.raises(ValueError):
        settle_cards(0, 1, yellows=1, dismissed=False, serving=True, completed=True)


def test_real_engine_second_yellow_awards_one_suspension():
    state, _ = simulate_match(
        replace(
            demo_match(),
            policy=EnginePolicy(yellow_bps=10000, direct_red_bps=0, injury_bps=0, foul_bps=0),
        )
    )
    dismissed = [c for t in (state.home, state.away) for c in t.conditions if c.yellows == 2]
    assert dismissed
    for c in dismissed:
        change = settle_cards(
            1, 0, yellows=c.yellows, dismissed=c.dismissed, serving=False, completed=False
        )
        assert change.awarded == 1 and change.yellows == 1


async def test_scope_is_edition_league_or_whole_champions_tournament():
    season, league, home, away, tournament, tie_id = (uuid4() for _ in range(6))
    fixture = Fixture(
        id=uuid4(),
        season_id=season,
        league_id=league,
        competition="league",
        round_number=1,
        home_club_id=home,
        away_club_id=away,
    )
    plan = FixturePlan(
        id=fixture.id,
        season_id=season,
        league_id=league,
        competition="league",
        round_number=1,
        home_club_id=home,
        away_club_id=away,
        announced_at=datetime(2026, 10, 23, tzinfo=UTC),
    )
    tie = ChampionsTie(id=tie_id, tournament_id=tournament, season_id=season)

    async def get(model, key):
        return plan if model is FixturePlan else tie if model is ChampionsTie else None

    session = SimpleNamespace(get=AsyncMock(side_effect=get))
    assert await tournament_scope(session, fixture) == f"league:{season}:{league}"
    fixture.league_id = plan.league_id = uuid4()
    assert await tournament_scope(session, fixture) != f"league:{season}:{league}"
    fixture.competition = plan.competition = "cup"
    fixture.league_id = plan.league_id = None
    assert await tournament_scope(session, fixture) == f"cup:{season}"
    fixture.competition = plan.competition = "champions"
    plan.conditional_ref = f"champions-tie:{tie_id}:leg:1"
    assert await tournament_scope(session, fixture) == f"champions:{tournament}"
    fixture.round_number = plan.round_number = 2
    plan.conditional_ref = f"champions-tie:{tie_id}:leg:2"
    assert await tournament_scope(session, fixture) == f"champions:{tournament}"
    fixture.competition = "friendly"
    assert await tournament_scope(session, fixture) is None
    fixture.competition = "league"
    session.get = AsyncMock(return_value=None)
    assert await tournament_scope(session, fixture) is None
