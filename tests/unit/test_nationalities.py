from uuid import UUID

from goaletto.domain.nationalities import COUNTRIES, flag, legacy_nationality, nationality


def test_nationality_is_stable_and_flags_are_only_flags():
    person = UUID("11111111-1111-1111-1111-111111111111")
    assert (
        legacy_nationality(person) == "SE"
    )  # Frozen nationality-v1 reference, independent of names.
    assert flag("RS") == "🇷🇸"
    assert flag("BR") == "🇧🇷"
    assert flag(None) == flag("ZZ") == "🌐"
    assert all(len(flag(c)) == 2 for c in COUNTRIES)


def test_new_country_distribution_includes_eastern_europe_and_naming_coverage():
    from uuid import NAMESPACE_URL, uuid5

    from goaletto.domain.person_names import COUNTRY_POOLS

    values = {nationality(uuid5(NAMESPACE_URL, f"country-check:{i}")) for i in range(10000)}
    assert values == set(COUNTRIES) == set(COUNTRY_POOLS)
    assert {"UA", "RU", "BY", "GE", "KZ", "AM"} <= values
    assert [flag(c) for c in ("UA", "RU", "BY")] == ["🇺🇦", "🇷🇺", "🇧🇾"]


def test_national_names_keep_whole_traditions_and_about_ten_percent_foreign_origins():
    from uuid import NAMESPACE_URL, uuid5

    from goaletto.domain.person_names import COUNTRY_POOLS, POOLS, generated_name

    migrants = 0
    for i in range(10000):
        key = uuid5(NAMESPACE_URL, f"name-check:{i}")
        country = COUNTRIES[i % len(COUNTRIES)]
        profile = generated_name(key, country)
        assert profile == generated_name(key, country)
        assert profile.country == country
        assert profile.tradition in COUNTRY_POOLS[profile.origin]
        if profile.migrant:
            migrants += 1
            assert profile.tradition not in COUNTRY_POOLS[country]
        else:
            assert profile.tradition in COUNTRY_POOLS[country]
        first, last = POOLS[profile.tradition]
        assert profile.name in {f"{a} {b}" for a in first for b in last}
    assert 900 <= migrants <= 1100


def test_starting_world_resolves_names_without_changing_background_or_skills():
    from dataclasses import replace
    from datetime import UTC, datetime

    from goaletto.domain.person_names import generated_name
    from goaletto.domain.world import WorldOptions, build_world

    options = WorldOptions(
        key="national-names", seed=71, first_kickoff=datetime(2030, 1, 12, 18, tzinfo=UTC)
    )
    world = build_world(options)
    legacy = build_world(replace(options, naming_policy="international-v1"))
    assert len({p.name for p in world.players}) == 1000
    for p, old in zip(world.players, legacy.players, strict=True):
        assert (p.id, p.skills, p.age, p.position, p.retirement) == (
            old.id,
            old.skills,
            old.age,
            old.position,
            old.retirement,
        )
        assert any(generated_name(p.id, attempt=i).name == p.name for i in range(256))
