"""Starter strength, development competition and frozen identity draws."""

import hashlib
import json
from dataclasses import asdict, replace
from random import Random
from statistics import median
from uuid import UUID

import pytest

from goaletto.domain.academy import candidate
from goaletto.domain.finances import PRIMARY
from goaletto.domain.scouting import discovery
from goaletto.domain.starter_squads import (
    LEGACY_STARTER_POLICY,
    STARTER_POLICY,
    rebase_skills,
    starter_skills,
)
from goaletto.domain.world import build_world
from tests.unit.test_world import OPTIONS


def quality(skills, position):
    return (3 * skills[PRIMARY[position]] + skills[6] + skills[7]) / 5


def test_existing_identity_and_retirement_draws_survive_new_starter_policy():
    old = build_world(replace(OPTIONS, starter_policy=LEGACY_STARTER_POLICY))
    new = build_world(OPTIONS)
    for before, after in zip(old.players, new.players, strict=True):
        assert replace(before, skills=after.skills) == after
        assert before.skills[8:] == after.skills[8:]
    assert old.leagues[0].clubs[0].id == new.leagues[0].clubs[0].id
    assert old.options.fingerprint != new.options.fingerprint


def test_legacy_fingerprint_does_not_add_policy_to_original_configuration():
    options = replace(OPTIONS, starter_policy=LEGACY_STARTER_POLICY)
    # The exact original fingerprint contract, before starter_policy existed.
    data = asdict(options)
    data.pop("starter_policy")
    data["first_kickoff"] = options.first_kickoff.isoformat()
    data["policy"] = "world-seed-v1"
    assert (
        options.fingerprint == hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    )


@pytest.mark.parametrize("position", ("GK", "DL", "DC", "DR", "ML", "MC", "MR", "FC"))
def test_same_draws_and_whole_earned_growth_survive_rebase(position):
    for seed in range(40):
        old_rng, new_rng = Random(seed), Random(seed)
        old = starter_skills(position, old_rng, LEGACY_STARTER_POLICY)
        new = starter_skills(position, new_rng, STARTER_POLICY)
        assert old_rng.getstate() == new_rng.getstate()
        current = tuple(v + 2 for v in old[:8])
        assert rebase_skills(current, old[:8], new[:8]) == tuple(v + 2 for v in new[:8])


def test_academy_one_and_weak_scout_can_compete_with_free_starters():
    positions = ("GK", "DL", "DC", "DR", "ML", "MC", "MR", "FC")
    starters = [quality(starter_skills(p, Random(i)), p) for i in range(200) for p in positions]
    academy = [
        candidate(UUID(int=i), UUID(int=9999), s, 1) for i in range(1, 201) for s in (1, 2, 3)
    ]
    scouts = [
        quality(discovery(UUID(int=i), p, 1).skills, p) for i in range(1, 201) for p in positions
    ]
    assert 5.5 <= median(starters) <= 6
    assert median(quality(p.skills, p.position) for p in academy) > median(starters)
    assert median(scouts) > median(starters)
    assert all(p.age == 17 for p in academy)


def test_rebase_rejects_unproven_below_original_profile():
    with pytest.raises(ValueError, match="below"):
        rebase_skills((3,) * 8, (4,) * 8, (2,) * 8)
