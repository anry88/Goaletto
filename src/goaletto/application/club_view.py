"""Read-only club/squad/player views. No Telegram, network or wall-clock reads.

Queries always recheck the current ownership under lock and record an explicit
game action receipt, so a former owner's stale buttons cannot read the club
after release. Analytics opt-out never disables the operational receipt.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    owned_club,
    user_effect,
)
from goaletto.infrastructure.models import (
    AnalyticsManager,
    AppearanceIdentity,
    Club,
    ClubLineup,
    League,
    LineupSlot,
    Manager,
    Person,
    Player,
)

CALLBACK_NAMESPACE = UUID("0e9f4f0e-9a1b-5c9a-9d2e-9b6b1a2c3d4e")
SQUAD_PAGE_SIZE = 5
ACTIVE_SQUAD_STATUSES = ("active", "last_season")
NEUTRAL_KIT_PRIMARY = "#444444"
NEUTRAL_KIT_SECONDARY = "#DDDDDD"
NEUTRAL_KIT_VERSION = 1


@dataclass(frozen=True)
class ClubCard:
    id: UUID
    name: str
    tier: int
    budget: int
    stadium_capacity: int
    supporters: int
    kit_primary: str
    kit_secondary: str
    kit_version: int
    vip: bool = False
    country_code: str | None = None


@dataclass(frozen=True)
class SquadMember:
    person_id: UUID
    name: str
    age: int
    position: str
    form: int
    status: str
    slot: int | None
    country_code: str | None = None
    slot_position: str | None = None
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class SquadPage:
    club: ClubCard
    members: tuple[SquadMember, ...]
    page: int
    total_pages: int
    total_members: int
    formation: str | None = None


@dataclass(frozen=True)
class PlayerCard:
    person_id: UUID
    name: str
    age: int
    position: str
    alternative_positions: tuple[str, ...]
    goalkeeping: int
    defending: int
    playmaking: int
    winger: int
    attacking: int
    set_pieces: int
    stamina: int
    pace: int
    leadership: int
    loyalty: int
    form: int
    experience: int
    morale: int
    status: str
    injured: bool
    club_id: UUID | None
    club_name: str | None
    appearance_identity_id: str
    appearance_variant_id: str
    catalog_version: int
    kit_primary: str
    kit_secondary: str
    kit_version: int
    vip: bool = False
    energy_bps: int = 10000
    energy_full_at: datetime | None = None
    country_code: str | None = None
    ownership_id: UUID | None = None


async def actor_for_callback(
    session: AsyncSession,
    *,
    telegram_id: int,
    callback_id: str,
    at: datetime,
    update_id: int | None = None,
) -> Actor:
    """Build an Actor for a callback query without trusting client-side IDs.

    The operation is derived from the Telegram callback query id, so each
    delivery is idempotent while carrying no club/player identity. Callers
    must still resolve the club from the current ownership, never from
    callback payload.
    """
    require_utc(at)
    if type(telegram_id) is not int or not 0 < telegram_id < 2**63:
        raise PermissionDenied("Invalid Telegram identity")
    if type(callback_id) is not str or not callback_id or len(callback_id) > 128:
        raise PermissionDenied("Invalid callback identity")
    if not session.in_transaction():
        raise ValueError("Club views need a transaction")
    manager = await session.scalar(select(Manager).where(Manager.telegram_id == telegram_id))
    if manager is None:
        raise PermissionDenied("Start is required")
    analytics_id = await session.scalar(
        select(AnalyticsManager.id).where(AnalyticsManager.manager_id == manager.id)
    )
    operation = uuid5(CALLBACK_NAMESPACE, f"goaletto:callback:v1:{callback_id}")
    return Actor(manager.id, manager.telegram_id, analytics_id, operation, at, update_id)


def _club_card(club: Club, tier: int, vip: bool = False) -> ClubCard:
    if type(tier) is not int or not 1 <= tier <= 5:
        raise ValueError("Invalid league tier")
    return ClubCard(
        id=club.id,
        name=club.name,
        tier=tier,
        budget=club.budget,
        stadium_capacity=club.stadium_capacity,
        supporters=club.supporters,
        kit_primary=club.kit_primary,
        kit_secondary=club.kit_secondary,
        kit_version=club.kit_version,
        vip=vip,
        country_code=club.country_code,
    )


async def get_owned_club(session: AsyncSession, actor: Actor, *, record: bool = True) -> ClubCard:
    if not session.in_transaction():
        raise ValueError("Club views need a transaction")
    ownership, club = await owned_club(session, actor)
    league = await session.scalar(select(League).where(League.id == club.league_id))
    if league is None:
        raise ValueError("Club league is missing")
    if record:
        await user_effect(
            session, actor, "club_opened", Surface.CLUB, ownership=ownership, club_id=club.id
        )
    from goaletto.application.cosmetics import vip_payment

    return _club_card(club, league.tier, await vip_payment(session, club.id, actor.at) is not None)


async def get_squad_page(session: AsyncSession, actor: Actor, page: int) -> SquadPage:
    if type(page) is not int or page < 1 or page > 20:
        raise ValueError("Invalid squad page")
    if not session.in_transaction():
        raise ValueError("Club views need a transaction")
    ownership, club = await owned_club(session, actor)
    league = await session.scalar(select(League).where(League.id == club.league_id))
    if league is None:
        raise ValueError("Club league is missing")
    card = _club_card(club, league.tier)
    slot_rows = (
        await session.scalars(select(LineupSlot).where(LineupSlot.club_id == club.id))
    ).all()
    player_rows = (
        await session.scalars(
            select(Player).where(
                Player.club_id == club.id, Player.status.in_(ACTIVE_SQUAD_STATUSES)
            )
        )
    ).all()
    person_ids = [row.person_id for row in player_rows]
    persons: dict[UUID, Person] = {}
    if person_ids:
        for person in (
            await session.scalars(select(Person).where(Person.id.in_(person_ids)))
        ).all():
            persons[person.id] = person
    slots = {row.person_id: row.slot for row in slot_rows}
    slot_positions = {row.person_id: row.position for row in slot_rows if row.slot <= 11}
    lineup = await session.scalar(select(ClubLineup).where(ClubLineup.club_id == club.id))

    def sort_key(row: Player) -> tuple[int, str, str]:
        person = persons.get(row.person_id)
        name = person.name if person is not None else ""
        return (slots.get(row.person_id, 999), name, str(row.person_id))

    ordered = sorted(player_rows, key=sort_key)
    total = len(ordered)
    total_pages = max(1, (total + SQUAD_PAGE_SIZE - 1) // SQUAD_PAGE_SIZE)
    if page > total_pages:
        raise ValueError("Invalid squad page")
    members: list[SquadMember] = []
    for row in ordered[(page - 1) * SQUAD_PAGE_SIZE : page * SQUAD_PAGE_SIZE]:
        member_person = persons.get(row.person_id)
        if member_person is None:
            raise ValueError("Squad person is missing")
        members.append(
            SquadMember(
                person_id=row.person_id,
                name=member_person.name,
                country_code=member_person.country_code,
                age=member_person.age,
                position=row.position,
                form=row.form,
                status=row.status,
                slot=slots.get(row.person_id),
                slot_position=slot_positions.get(row.person_id),
                alternatives=tuple(row.alternative_positions or ()),
            )
        )
    await user_effect(
        session,
        actor,
        "lineup_opened",
        Surface.SQUAD,
        ownership=ownership,
        club_id=club.id,
    )
    return SquadPage(
        card,
        tuple(members),
        page,
        total_pages,
        total,
        formation=lineup.formation if lineup is not None else None,
    )


async def get_player_card(session: AsyncSession, actor: Actor, person_id: UUID) -> PlayerCard:
    if not isinstance(person_id, UUID):
        raise ValueError("Invalid player identity")
    if not session.in_transaction():
        raise ValueError("Club views need a transaction")
    ownership, club = await owned_club(session, actor)
    player = await session.scalar(select(Player).where(Player.person_id == person_id))
    if player is None:
        raise PermissionDenied("Player is not available")
    person = await session.get(Person, person_id)
    if person is None:
        raise PermissionDenied("Player is not available")
    if player.status not in ("active", "last_season", "retired"):
        raise ValueError("Unknown player status")
    if player.status == "retired":
        from goaletto.application.history import get_player_matches

        await get_player_matches(session, actor, person_id, 1)
        # History view: no game actions, no club match required. The caller
        # must already hold a current ownership (checked above), so a former
        # owner's stale buttons fail before reaching this branch.
        kit_primary, kit_secondary, kit_version = (
            NEUTRAL_KIT_PRIMARY,
            NEUTRAL_KIT_SECONDARY,
            NEUTRAL_KIT_VERSION,
        )
        club_id: UUID | None = None
        club_name: str | None = None
    else:
        if player.club_id != club.id:
            raise PermissionDenied("Player does not belong to the current club")
        kit_primary, kit_secondary, kit_version = (
            club.kit_primary,
            club.kit_secondary,
            club.kit_version,
        )
        club_id = club.id
        club_name = club.name
    identity = await session.scalar(
        select(AppearanceIdentity).where(AppearanceIdentity.id == person.appearance_identity_id)
    )
    if identity is None:
        raise ValueError("Portrait identity is missing")
    await user_effect(
        session,
        actor,
        "lineup_opened",
        Surface.SQUAD,
        ownership=ownership,
        club_id=club.id,
    )
    from goaletto.application.recovery import EnergyStatus, energy_status

    energy = (
        await energy_status(session, person.id, actor.at)
        if player.status != "retired"
        else EnergyStatus(10000, None)
    )
    return PlayerCard(
        person_id=person.id,
        ownership_id=ownership.id,
        name=person.name,
        country_code=person.country_code,
        age=person.age,
        position=player.position,
        alternative_positions=tuple(player.alternative_positions or ()),
        goalkeeping=player.goalkeeping,
        defending=player.defending,
        playmaking=player.playmaking,
        winger=player.winger,
        attacking=player.attacking,
        set_pieces=player.set_pieces,
        stamina=player.stamina,
        pace=player.pace,
        leadership=player.leadership,
        loyalty=player.loyalty,
        form=player.form,
        experience=player.experience,
        morale=player.morale,
        status=player.status,
        injured=player.injured_until is not None and player.injured_until > actor.at,
        club_id=club_id,
        club_name=club_name,
        appearance_identity_id=person.appearance_identity_id,
        appearance_variant_id=person.appearance_variant_id,
        catalog_version=identity.catalog_version,
        energy_bps=energy.bps,
        energy_full_at=energy.full_at,
        kit_primary=kit_primary,
        kit_secondary=kit_secondary,
        kit_version=kit_version,
    )
