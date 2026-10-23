from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from goaletto.application.persons import AppearanceExhausted
from goaletto.application.world import prepare_world, world_summary
from goaletto.application.world_portraits import age_band, assign_world_portraits
from goaletto.domain.world import PRIMARY_SKILL, ROSTER, SKILL_NAMES, WorldOptions, build_world
from goaletto.portraits.whole import load_whole_catalog

OPTIONS = WorldOptions(
    key="initial",
    seed=0,
    first_kickoff=datetime(2026, 10, 17, 18, tzinfo=UTC),
)


def test_world_population_coverage_and_equal_baseline() -> None:
    plan = build_world(OPTIONS)
    assert len(plan.leagues) == 5
    clubs = [club for league in plan.leagues for club in league.clubs]
    assert len({club.id for club in clubs}) == 50
    assert len({p.id for p in plan.players}) == 1000
    assert len({p.name for p in plan.players}) == 1000
    sums = []
    for league in plan.leagues:
        assert len(league.clubs) == 10
        assert {club.tie_break_order for club in league.clubs} == set(range(1, 11))
        for club in league.clubs:
            assert len(club.players) == 20
            assert [(p.position, p.age) for p in club.players] == list(ROSTER)
            assert Counter(p.position for p in club.players)["GK"] == 2
            starters = club.players[:11]
            assert Counter(p.position for p in starters) == {
                "GK": 1,
                "DL": 1,
                "DC": 2,
                "DR": 1,
                "ML": 1,
                "MC": 2,
                "MR": 1,
                "FC": 2,
            }
            for p in club.players:
                assert all(1 <= skill <= 20 for skill in p.skills)
                assert p.skills[SKILL_NAMES.index(PRIMARY_SKILL[p.position])] in (6, 7)
            sums.append(sum(sum(p.skills[:8]) for p in club.players))
    assert len(set(sums)) == 1  # No top-tier starting advantage.
    assert len({p.skills for p in plan.players}) > 20


def test_reproducible_seed_and_independent_set_identifiers() -> None:
    assert build_world(OPTIONS) == build_world(OPTIONS)
    different = build_world(replace(OPTIONS, seed=12))
    assert different.options.fingerprint != OPTIONS.fingerprint
    assert different.players != build_world(OPTIONS).players
    second = build_world(replace(OPTIONS, key="second"))
    assert {p.id for p in second.players}.isdisjoint(p.id for p in build_world(OPTIONS).players)


def test_all_faces_are_unique_age_compatible_and_reproducible() -> None:
    plan = build_world(OPTIONS)
    recipes = assign_world_portraits(plan)
    catalog = load_whole_catalog()
    assert recipes == assign_world_portraits(plan)
    assert len({recipe.identity_id for recipe in recipes.values()}) == 1000
    for p in plan.players:
        recipe = recipes[p.id]
        variant = catalog["variants"][recipe.variant_id]
        assert variant["identity_id"] == recipe.identity_id
        assert variant["bank"] == "players"
        assert variant.get("stage", age_band(variant["depicted_age"])) == age_band(p.age)


def test_another_set_never_recycles_used_faces() -> None:
    plan = build_world(OPTIONS)
    used = frozenset(recipe.identity_id for recipe in assign_world_portraits(plan).values())
    with pytest.raises(AppearanceExhausted):
        assign_world_portraits(build_world(replace(OPTIONS, key="second")), reserved=used)


def test_missing_age_coverage_does_not_fall_back_to_foreign_or_wrong_age_faces(monkeypatch) -> None:
    plan = build_world(OPTIONS)
    catalog = load_whole_catalog()
    catalog["variants"] = {
        key: value for key, value in catalog["variants"].items() if not value.get("stage")
    }
    for identity in catalog["identities"].values():
        identity["variants"] = [key for key in identity["variants"] if key in catalog["variants"]]
    monkeypatch.setattr(
        "goaletto.application.world_portraits.load_whole_catalog", lambda _: catalog
    )
    # Bare bank has 286 adult faces, while the common squad template needs 350.
    with pytest.raises(AppearanceExhausted, match="age-compatible"):
        assign_world_portraits(plan)


def test_prepared_world_has_no_owners_or_extra_staff_population() -> None:
    world = prepare_world(OPTIONS)
    summary = world_summary(world)
    assert summary["players"] == summary["unique_faces"] == 1000
    assert summary["fixtures"] == 450
    assert summary["held_jobs"] == 53
    assert summary["core_skill_spread"] == 0
    assert all(p.retirement.is_last_season for p in world.plan.players if p.age == 38)


@pytest.mark.parametrize(
    "key,seed,budget", [("Bad key", 0, 0), ("a", True, 0), ("a", -1, 0), ("a", 0, -1)]
)
def test_invalid_seed_configuration(key: str, seed: int, budget: int) -> None:
    with pytest.raises(ValueError):
        WorldOptions(
            key=key,
            seed=seed,
            initial_budget=budget,
            first_kickoff=OPTIONS.first_kickoff,
        )
