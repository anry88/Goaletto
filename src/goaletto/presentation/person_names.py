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
