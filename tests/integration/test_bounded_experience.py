from copy import deepcopy
from datetime import timedelta

import pytest
from sqlalchemy import select

from goaletto.application.results import finalize_result
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.match_ratings import sector_ratings
from goaletto.domain.match_types import EnginePolicy
from goaletto.infrastructure.models import Match, MatchDevelopment, Player
from tests.integration.test_agent_wave import start_fixture
from tests.integration.test_candidates import funded
from tests.integration.test_match_practice_runtime import completed_checkpoint

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("version", [6, 7])
async def test_new_v7_and_already_pinned_v6_keep_distinct_effects_and_identical_growth(
    sessions, monkeypatch, version
):
    import goaletto.application.match_runtime as runtime

    original = runtime.start_match

    def start(*args, **kwargs):
        kwargs.update(
            engine_version=f"regulation-v{version}",
            policy=EnginePolicy(version=f"match-policy-v{version}"),
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime, "start_match", start)
    _, _, club = await funded(sessions)
    async with sessions.begin() as s:
        for p in (await s.scalars(select(Player))).all():
            p.experience = 50
            p.position_experience = {}
    fixture, at = await start_fixture(sessions, club)
    async with sessions.begin() as s:
        m = await s.scalar(select(Match).where(Match.fixture_id == fixture))
        frozen = deepcopy(m.starting_snapshot)
        state = restore_state(frozen)
        assert m.engine_version == f"regulation-v{version}" and m.state_version == version
        assert all(
            not p.position_experience for t in (state.home, state.away) for p in t.snapshot.players
        )
        assert (sector_ratings(state.home, state.policy).goalkeeper > 0) == (version == 7)
        pinned = await s.get(MatchDevelopment, m.id)
        assert pinned.policy_version == "match-practice-v5"
        assert pinned.terms_snapshot["experience_roster"]
    final = await completed_checkpoint(sessions, fixture, at)
    async with sessions.begin() as s:
        assert await finalize_result(s, fixture, at + timedelta(hours=1))
        pinned = await s.get(MatchDevelopment, final.match_id)
        results = deepcopy(pinned.results)
        assert any(r["experience_gain"] > 0 for r in results)
        for row in results:
            for values in row["position_experience"].values():
                assert values["before"] == 0
                assert values["after"] == values["gain"]
        assert not await finalize_result(s, fixture, at + timedelta(hours=2))
        assert pinned.results == results
        m = await s.get(Match, final.match_id)
        assert m.starting_snapshot == frozen
        assert state_hash(restore_state(m.state)) == state_hash(final)
