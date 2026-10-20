"""One immutable club choice; only future academy intakes inherit it."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club, user_effect
from goaletto.domain.nationalities import COUNTRIES


@dataclass(frozen=True)
class CountryScreen:
    ownership_id: UUID
    club_name: str
    country_code: str | None


async def screen(
    session: AsyncSession, actor: Actor, ownership_id: UUID | None = None, *, record: bool = True
) -> CountryScreen:
    owner, club = await owned_club(session, actor)
    if ownership_id is not None and ownership_id != owner.id:
        raise PermissionDenied("Stale country choice")
    if record:
        await user_effect(
            session, actor, "club_opened", Surface.CLUB, ownership=owner, club_id=club.id
        )
    return CountryScreen(owner.id, club.name, club.country_code)


async def choose(
    session: AsyncSession, actor: Actor, ownership_id: UUID, country_code: str
) -> CountryScreen:
    if country_code not in COUNTRIES:
        raise ValueError("Unknown club country")
    owner, club = await owned_club(session, actor)
    if owner.id != ownership_id:
        raise PermissionDenied("Stale country choice")
    if club.country_code is not None and club.country_code != country_code:
        raise ValueError("Club country already selected")
    if club.country_code is None:
        club.country_code = country_code
        club.country_selected_at = actor.at
        club.country_ownership_id = owner.id
    await user_effect(session, actor, "club_opened", Surface.CLUB, ownership=owner, club_id=club.id)
    await session.flush()
    return CountryScreen(owner.id, club.name, club.country_code)
