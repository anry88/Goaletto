from uuid import uuid4

import pytest

from goaletto.domain.academy import (
    CANDIDATE_AGE,
    MAX_LEVEL,
    MIN_LEVEL,
    academy_upkeep,
    candidate,
    candidate_count,
    improve_profile,
    sign_limit,
    skill_band,
    skill_snapshot,
    upgrade_cost,
)
from goaletto.domain.development import SKILLS
from goaletto.domain.match_types import POSITIONS
from goaletto.domain.world import SKILL_NAMES


def test_levels_are_bounded_and_monotonic() -> None:
    assert (MIN_LEVEL, MAX_LEVEL) == (1, 5)
    upkeeps = [academy_upkeep(level) for level in range(MIN_LEVEL, MAX_LEVEL + 1)]
    limits = [sign_limit(level) for level in range(MIN_LEVEL, MAX_LEVEL + 1)]
    assert upkeeps == sorted(upkeeps)
    assert limits == sorted(limits)
    assert all(3 <= candidate_count(level) <= 5 for level in range(MIN_LEVEL, MAX_LEVEL + 1))


def test_level_functions_reject_bad_levels() -> None:
    for level in (0, 6, -1, 21):
        for function in (academy_upkeep, sign_limit, candidate_count, skill_band):
            with pytest.raises(ValueError):
                function(level)
    with pytest.raises(ValueError):
        upgrade_cost(0)


def test_upgrade_cost_reaches_max_and_then_stops() -> None:
    assert upgrade_cost(MAX_LEVEL - 1) == 2_500_000
    with pytest.raises(ValueError):
        upgrade_cost(MAX_LEVEL)


def test_candidates_are_deterministic_and_seeded_per_slot() -> None:
    club, season = uuid4(), uuid4()
    assert candidate(club, season, 1, 3) == candidate(club, season, 1, 3)
    assert candidate(club, season, 1, 3).person_id != candidate(club, season, 2, 3).person_id
    assert candidate(club, season, 1, 3).person_id != candidate(uuid4(), season, 1, 3).person_id


def test_candidate_fields_are_consistent_with_the_level_band() -> None:
    club, season = uuid4(), uuid4()
    for level in range(MIN_LEVEL, MAX_LEVEL + 1):
        for slot in range(1, candidate_count(level) + 1):
            person = candidate(club, season, slot, level)
            assert person.age == CANDIDATE_AGE
            assert person.position in POSITIONS
            assert person.position not in person.alternatives
            assert all(alternative in POSITIONS for alternative in person.alternatives)
            snapshot = skill_snapshot(person.skills)
            assert set(snapshot) == set(SKILL_NAMES)
            assert all(1 <= value <= 20 for value in snapshot.values())
            assert set(person.caps) == set(SKILLS)
            assert all(person.caps[skill] >= snapshot[skill] for skill in SKILLS)


def test_invalid_slots_and_identities_are_rejected() -> None:
    club, season = uuid4(), uuid4()
    for slot in (0, candidate_count(1) + 1, -1):
        with pytest.raises(ValueError):
            candidate(club, season, slot, 1)
    with pytest.raises(ValueError):
        candidate("club", season, 1, 1)  # type: ignore[arg-type]


def test_skill_snapshot_rejects_wrong_shapes() -> None:
    with pytest.raises(ValueError):
        skill_snapshot((1, 2, 3))
    with pytest.raises(ValueError):
        skill_snapshot(tuple(0 for _ in SKILL_NAMES))
    with pytest.raises(ValueError):
        skill_snapshot((*([5] * (len(SKILL_NAMES) - 1)), True))


def test_upgrades_raise_profiles_monotonically_without_rerolling() -> None:
    club, season = uuid4(), uuid4()
    for slot in range(1, 4):
        profile = candidate(club, season, slot, 1)
        skills, caps = skill_snapshot(profile.skills), profile.caps
        initial = skills.copy()
        for level in range(2, 6):
            updated, potential = improve_profile(skills, caps, profile.position, level - 1, level)
            assert all(updated[name] >= value for name, value in skills.items())
            assert all(value <= 20 and value >= updated[name] for name, value in potential.items())
            assert all(potential[name] >= value for name, value in caps.items())
            assert improve_profile(updated, potential, profile.position, level, level) == (
                updated,
                potential,
            )
            skills, caps = updated, potential
        assert skills != initial
        if profile.position != "GK":
            assert skills["goalkeeping"] == initial["goalkeeping"]
        with pytest.raises(ValueError, match="only improve"):
            improve_profile(skills, caps, profile.position, 5, 4)
