from uuid import UUID

from goaletto.domain.nationalities import COUNTRIES, flag, nationality


def test_nationality_is_stable_and_flags_are_only_flags():
    person = UUID("11111111-1111-1111-1111-111111111111")
    assert nationality(person) == "SE"  # Frozen nationality-v1 reference, independent of names.
    assert flag("RS") == "🇷🇸"
    assert flag("BR") == "🇧🇷"
    assert flag(None) == flag("ZZ") == "🌐"
    assert all(len(flag(c)) == 2 for c in COUNTRIES)
