"""Durable person/face allocation inside the caller's PostgreSQL transaction."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.domain.nationalities import COUNTRIES, nationality
from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import AppearanceIdentity, AppearanceVariant, Person


class AppearanceExhausted(ValueError):
    pass


@dataclass(frozen=True, kw_only=True)
class NewPerson:
    id: UUID
    creation_key: str
    name: str
    age: int
    career: str
    origin_bank: str
    # Caller chooses reviewed, age-appropriate variants; no random wrong-age fallback.
    eligible_variant_ids: tuple[str, ...]
    home_league_set_id: UUID | None = None
    country_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("Person ID must be a UUID")
        if not self.creation_key or len(self.creation_key) > 200:
            raise ValueError("Invalid person creation key")
        if not self.name.strip() or len(self.name) > 120:
            raise ValueError("Invalid person name")
        if self.country_code is not None and self.country_code not in COUNTRIES:
            raise ValueError("Unknown person country")
        if type(self.age) is not int or not 0 <= self.age <= 150:
            raise ValueError("Invalid person age")
        if self.career not in {"player", "staff"}:
            raise ValueError("New people must start a player or staff career")
        if self.career == "player" and not 17 <= self.age <= 38:
            raise ValueError("New footballers must be aged 17 through 38")
        if self.career == "staff" and self.age > 70:
            raise ValueError("Staff cannot start after their final work season")
        expected_bank = "players" if self.career == "player" else "staff"
        if self.origin_bank != expected_bank:
            raise ValueError("A new person must use the matching origin bank")
        if not self.eligible_variant_ids or len(set(self.eligible_variant_ids)) != len(
            self.eligible_variant_ids
        ):
            raise ValueError("Supply distinct eligible portrait variants")


async def create_person(session: AsyncSession, request: NewPerson) -> Person:
    """No commit here: person, profile, world seed and events must commit together."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
    )
    existing = await session.scalar(
        select(Person).where(Person.creation_key == request.creation_key)
    )
    if existing is not None:
        # A retry never changes an already-aged, transferred or retired person's state.
        if existing.id != request.id:
            raise ValueError("Creation key belongs to a different person")
        return existing
    usage = (
        select(func.count(Person.id))
        .where(Person.appearance_identity_id == AppearanceIdentity.id)
        .correlate(AppearanceIdentity)
        .scalar_subquery()
    )
    variant = await session.scalar(
        select(AppearanceVariant)
        .join(AppearanceIdentity, AppearanceIdentity.id == AppearanceVariant.identity_id)
        .where(
            AppearanceIdentity.origin_bank == request.origin_bank,
            AppearanceVariant.bank == request.origin_bank,
            AppearanceVariant.id.in_(request.eligible_variant_ids),
        )
        .order_by(usage, AppearanceIdentity.id, AppearanceVariant.id)
        .limit(1)
    )
    if variant is None:
        raise AppearanceExhausted("No eligible portrait in the approved catalog")
    person = Person(
        id=request.id,
        creation_key=request.creation_key,
        country_code=request.country_code or nationality(request.id),
        name=request.name,
        age=request.age,
        career=request.career,
        appearance_identity_id=variant.identity_id,
        appearance_variant_id=variant.id,
        home_league_set_id=request.home_league_set_id,
    )
    session.add(person)
    await session.flush()
    return person
