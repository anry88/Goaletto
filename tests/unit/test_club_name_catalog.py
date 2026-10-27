from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from goaletto.domain.club_name_catalog import ENGLISH_CLUB_NAMES, allocate_names
from goaletto.domain.club_names import validate_name
from goaletto.domain.world import WorldOptions, build_world

OPTIONS = WorldOptions(key="initial", seed=0, first_kickoff=datetime(2026, 10, 17, 18, tzinfo=UTC))


def test_catalog_and_whole_world_capacity_before_any_repetition():
    assert len(ENGLISH_CLUB_NAMES) == len(set(ENGLISH_CLUB_NAMES)) == 3200
    assert all(name.isascii() and validate_name(name) == name for name in ENGLISH_CLUB_NAMES)
    used = []
    for number in range(64):
        names = allocate_names(50, seed=f"set-{number}", existing=used)
        assert len(set(names)) == 50
        assert not set(names).intersection(used)
        used.extend(names)
    assert set(used) == set(ENGLISH_CLUB_NAMES)
    next_names = allocate_names(50, seed="overflow", existing=used)
    assert len(set(next_names)) == 50
    counts = Counter(used + list(next_names))
    assert set(counts.values()) == {1, 2}


def test_display_normalization_and_least_used_names_without_local_duplicates():
    low, *others = ENGLISH_CLUB_NAMES
    existing = [name for name in others for _ in range(3)]
    names = allocate_names(50, seed="skew", existing=existing)
    assert names[0] == low and len(set(names)) == 50
    assert low not in allocate_names(50, seed="skew", existing=[f"  {low.upper()}  "])
    assert names == allocate_names(50, seed="skew", existing=reversed(existing))


def test_name_policy_changes_only_names_and_fingerprint():
    new = build_world(OPTIONS)
    old_options = replace(OPTIONS, club_naming_policy=None)
    old = build_world(old_options)
    assert new.options.fingerprint != old.options.fingerprint
    assert new.players == old.players
    assert new.league_set_id == old.league_set_id and new.season_id == old.season_id
    for a, b in zip(new.leagues, old.leagues, strict=True):
        assert a.id == b.id
        assert tuple(replace(c, name="") for c in a.clubs) == tuple(
            replace(c, name="") for c in b.clubs
        )
    used = tuple(c.name for league in new.leagues for c in league.clubs)
    second = build_world(replace(OPTIONS, key="second"), existing_club_names=used)
    assert set(used).isdisjoint(c.name for league in second.leagues for c in league.clubs)


@pytest.mark.parametrize(
    "policy,fingerprint,world_hash",
    [
        (
            "legacy-v1",
            "2eeddba94773e3bdb3f11085413e027f7e19249a807d2694e722b16bf5053eaa",
            "d7bfdb16adfba6fc5a432155d67d3c8c4ab6d7499fbc04336c278bdd9de1e56a",
        ),
        (
            "international-v1",
            "072ed98e4024cbc780c3bbee3d9bdc1b49b93b9787280de8ff71815a26b5852a",
            "39c61ca1961cfa817fd8b68df3d9c7b032535145aa3980f5d0641a675a40ddb8",
        ),
        (
            "nationality-names-v2",
            "1fb5f23cddc0854ca859b97a256e9a56cdc48d9386aae0e9ab6542559cc49ba4",
            "9aeec251958f4b528005d0668ed54fb284e7988c38179743c9200de70939e97e",
        ),
    ],
)
def test_frozen_old_worlds(policy, fingerprint, world_hash):
    options = replace(OPTIONS, naming_policy=policy, club_naming_policy=None)
    assert options.fingerprint == fingerprint
    assert sha256(repr(build_world(options).leagues).encode()).hexdigest() == world_hash
