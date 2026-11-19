"""Display-only nationality flags; durable names/simulation snapshots stay unchanged."""

from typing import Protocol

from goaletto.domain.names import latin_name
from goaletto.domain.nationalities import flag_name


class NamedPerson(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def country_code(self) -> str | None: ...


def person_name(person: NamedPerson) -> str:
    return flag_name(latin_name(person.name), person.country_code)


def short_name(value: str) -> str:
    name = latin_name(value)
    parts = name.split(maxsplit=1)
    if len(parts) < 2:
        return name
    first, surname = parts
    if len(first) == 2 and first.endswith("."):
        return name
    return f"{first[0]}. {surname}"


def person_button_name(person: NamedPerson) -> str:
    return flag_name(short_name(person.name), person.country_code)
