from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.outbox_delivery import render_text, valid_payload
from goaletto.domain.candidates import (
    PLAYER_POSITIONS,
    POOL_POLICY,
    STAFF_ROLES,
    candidate_profile,
    deficit,
)
from goaletto.domain.names import international_catalog
from goaletto.domain.retirement import STAFF_PROBABILITIES_BPS, staff_last_season
from goaletto.domain.season_rollover import rollover_kickoff
from goaletto.domain.world import stable_seed
from goaletto.presentation.i18n import using_locale


@pytest.mark.parametrize("age,p", [(59, 0), *list(enumerate(STAFF_PROBABILITIES_BPS, 60))])
def test_staff_probability_boundaries(age, p):
    assert (
        not staff_last_season(age, p).is_last_season
        if p < 10000
        else staff_last_season(age, 9999).is_last_season
    )
    if p:
        assert staff_last_season(age, p - 1).is_last_season
    for draw in (0, 9999):
        decision = staff_last_season(age, draw)
        assert decision.probability_bps == p
        assert (decision.draw is None) == (p in (0, 10000))


def test_supply_deficit_never_exceeds_threshold():
    assert [deficit(n, 12) for n in (0, 11, 12, 13, 100)] == [12, 1, 0, 0, 0]
    assert deficit(0, 12, 1) == 1
    assert deficit(2, 2, 1) == 0
    with pytest.raises(ValueError):
        deficit(True, 12)


def test_rollover_calendar_rest_and_delayed_completion():
    first = datetime(2026, 10, 17, 18, tzinfo=UTC)
    finish = first + timedelta(weeks=17, minutes=33)
    assert rollover_kickoff(first, finish, finish + timedelta(seconds=1)) == first + timedelta(
        weeks=20
    )
    late = finish + timedelta(weeks=4, days=1)
    assert rollover_kickoff(first, late, late) == first + timedelta(weeks=24)
    at = first + timedelta(weeks=30, minutes=1)
    assert rollover_kickoff(first, finish, at) == first + timedelta(weeks=31)


@pytest.mark.parametrize("position", PLAYER_POSITIONS)
def test_new_baseline_players_cover_positions_reproducibly(position):
    person_id = uuid4()
    profile = candidate_profile(person_id, "player", position)
    assert profile == candidate_profile(person_id, "player", position)
    name, age, skills, fee, wage = profile
    seed = stable_seed(0, f"{POOL_POLICY}:{person_id}")
    assert name in {name for _, name in international_catalog(seed)}
    assert name and age == 18 and len(skills) == 10
    assert all(1 <= skill <= 20 for skill in skills)
    assert fee == 100000 and wage == 100 + 10 * sum(skills[:8])


@pytest.mark.parametrize("kind", ["player_lifecycle", "staff_lifecycle"])
@pytest.mark.parametrize("action", ["last_season", "retired"])
def test_lifecycle_notification_escapes_names_and_translates(kind, action):
    payload = dict(
        version=1,
        kind=kind,
        action=action,
        player_name="Name <b>",
        **{k: str(uuid4()) for k in ("ownership_id", "club_id", "person_id", "season_id")},
    )
    assert valid_payload(payload)
    for lang in ("ru", "en"):
        with using_locale(lang):
            rendered = render_text(payload)
        assert "&lt;b&gt;" in rendered and "Name <b>" not in rendered
    assert not valid_payload({**payload, "action": "unknown"})


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_new_staff_name_does_not_include_catalog_metadata(role):
    person_id = uuid4()
    seed = stable_seed(0, f"{POOL_POLICY}:{person_id}")
    assert candidate_profile(person_id, "staff", role)[0] in {
        name for _, name in international_catalog(seed)
    }
