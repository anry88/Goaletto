"""Owned academy intakes: durable shortlists, paid upgrades and youth promotion."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club, user_effect
from goaletto.application.money import debit_locked, locked_accounts
from goaletto.application.persons import AppearanceExhausted, NewPerson, create_person
from goaletto.domain.academy import (
    CANDIDATE_AGE,
    MAX_LEVEL,
    POLICY,
    academy_upkeep,
    candidate_count,
    sign_limit,
    skill_snapshot,
    upgrade_cost,
)
from goaletto.domain.academy import candidate as build_candidate
from goaletto.domain.training import POLICY_VERSION as TRAINING
from goaletto.domain.training import PROGRESS_KEYS
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    AcademyCandidate,
    AppearanceIdentity,
    AppearanceVariant,
    GameActionReceipt,
    Player,
    PlayerDevelopment,
    Season,
    SeasonClub,
)
from goaletto.portraits.whole import load_whole_catalog

ROSTER_LIMIT = 25


@dataclass(frozen=True)
class CandidateView:
    slot: int
    name: str
    age: int
    position: str
    alternatives: tuple[str, ...]
    skills: dict[str, int]
    caps: dict[str, int]
    status: str


@dataclass(frozen=True)
class AcademyScreen:
    club_name: str
    ownership_id: UUID
    level: int
    season_id: UUID
    available: int
    arrears: int
    upkeep: int
    next_upgrade_cost: int | None
    sign_limit: int
    signed: int
    roster_size: int
    candidates: tuple[CandidateView, ...]


async def current_season(session: AsyncSession, club_id: UUID) -> Season:
    season = await session.scalar(
        select(Season)
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(SeasonClub.club_id == club_id, Season.status.in_(("scheduled", "active")))
        .order_by(Season.number.desc())
        .limit(1)
    )
    if season is None:
        raise ValueError("Club has no current season")
    return season


async def ensure_intake(
    session: AsyncSession, club_id: UUID, season_id: UUID, level: int
) -> list[AcademyCandidate]:
    """Create the season shortlist once; retries reuse the persisted people."""
    rows = list(
        (
            await session.scalars(
                select(AcademyCandidate)
                .where(
                    AcademyCandidate.club_id == club_id,
                    AcademyCandidate.season_id == season_id,
                )
                .order_by(AcademyCandidate.slot)
            )
        ).all()
    )
    if rows:
        return rows
    for slot in range(1, candidate_count(level) + 1):
        profile = build_candidate(club_id, season_id, slot, level)
        session.add(
            AcademyCandidate(
                id=profile.person_id,
                club_id=club_id,
                season_id=season_id,
                slot=slot,
                policy_version=POLICY,
                name=profile.name,
                age=profile.age,
                position=profile.position,
                alternative_positions=list(profile.alternatives),
                skills=skill_snapshot(profile.skills),
                caps=profile.caps,
                status="available",
                signed_at=None,
            )
        )
    await session.flush()
    return list(
        (
            await session.scalars(
                select(AcademyCandidate)
                .where(
                    AcademyCandidate.club_id == club_id,
                    AcademyCandidate.season_id == season_id,
                )
                .order_by(AcademyCandidate.slot)
            )
        ).all()
    )


async def _roster_size(session: AsyncSession, club_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(Player).where(Player.club_id == club_id)
        )
        or 0
    )


def _view(row: AcademyCandidate) -> CandidateView:
    return CandidateView(
        slot=row.slot,
        name=row.name,
        age=row.age,
        position=row.position,
        alternatives=tuple(row.alternative_positions),
        skills=dict(row.skills),
        caps=dict(row.caps),
        status=row.status,
    )


async def load_academy(
    session: AsyncSession, actor: Actor, *, record: bool = True
) -> AcademyScreen:
    ownership, club = await owned_club(session, actor)
    account = (await locked_accounts(session, club.id))[club.id]
    season = await current_season(session, club.id)
    rows = await ensure_intake(session, club.id, season.id, club.academy_level)
    signed = sum(1 for row in rows if row.status == "signed")
    roster = await _roster_size(session, club.id)
    if record:
        await user_effect(
            session, actor, "academy_opened", Surface.CLUB, ownership=ownership, club_id=club.id
        )
    return AcademyScreen(
        club_name=club.name,
        ownership_id=ownership.id,
        level=club.academy_level,
        season_id=season.id,
        available=account.available,
        arrears=account.record.arrears,
        upkeep=academy_upkeep(club.academy_level),
        next_upgrade_cost=(
            upgrade_cost(club.academy_level) if club.academy_level < MAX_LEVEL else None
        ),
        sign_limit=sign_limit(club.academy_level),
        signed=signed,
        roster_size=roster,
        candidates=tuple(_view(row) for row in rows),
    )


async def upgrade_academy(session: AsyncSession, actor: Actor, ownership_id: UUID) -> int:
    ownership, club = await owned_club(session, actor)
    if ownership.id != ownership_id:
        raise PermissionDenied("Academy button belongs to an earlier owner")
    applied = await session.get(GameActionReceipt, (actor.operation_id, 0))
    if applied is not None:
        if applied.kind != "academy_upgraded":
            raise PermissionDenied("Action identity was reused with a different scope")
        return club.academy_level
    if club.academy_level >= MAX_LEVEL:
        raise ValueError("Academy is already at maximum level")
    cost = upgrade_cost(club.academy_level)
    account = (await locked_accounts(session, club.id))[club.id]
    await debit_locked(
        session,
        account,
        f"academy:upgrade:{actor.operation_id}",
        "academy_upgrade",
        cost,
        actor.at,
        details={
            "ownership_id": str(ownership.id),
            "from": club.academy_level,
            "to": club.academy_level + 1,
        },
    )
    club.academy_level += 1
    await session.flush()
    await user_effect(
        session, actor, "academy_upgraded", Surface.CLUB, ownership=ownership, club_id=club.id
    )
    return club.academy_level


async def sign_candidate(
    session: AsyncSession, actor: Actor, ownership_id: UUID, slot: int
) -> UUID:
    ownership, club = await owned_club(session, actor)
    if ownership.id != ownership_id:
        raise PermissionDenied("Academy button belongs to an earlier owner")
    season = await current_season(session, club.id)
    rows = await ensure_intake(session, club.id, season.id, club.academy_level)
    row = next((item for item in rows if item.slot == slot), None)
    if row is None:
        raise ValueError("Unknown academy candidate")
    if row.status == "signed":
        return row.id
    if row.status != "available":
        raise ValueError("Candidate is no longer available")
    if sum(1 for item in rows if item.status == "signed") >= sign_limit(club.academy_level):
        raise ValueError("Academy signing limit reached for the season")
    if await _roster_size(session, club.id) >= ROSTER_LIMIT:
        raise ValueError("Squad limit reached")
    variants = await _youth_variants(session)
    person = await create_person(
        session,
        NewPerson(
            id=row.id,
            creation_key=f"{POLICY}:{club.id}:{season.id}:{slot}",
            name=row.name,
            age=CANDIDATE_AGE,
            career="player",
            origin_bank="players",
            eligible_variant_ids=variants,
        ),
    )
    skills = {name: int(row.skills[name]) for name in SKILL_NAMES}
    session.add(
        Player(
            person_id=person.id,
            club_id=club.id,
            position=row.position,
            alternative_positions=list(row.alternative_positions),
            **skills,
            form=6,
            experience=0,
            morale=50,
            status="active",
        )
    )
    session.add(
        PlayerDevelopment(
            person_id=person.id,
            caps={name: int(value) for name, value in row.caps.items()},
            progress=dict.fromkeys(PROGRESS_KEYS, 0),
            policy_version=TRAINING,
        )
    )
    await session.flush()
    from goaletto.application.finances import salary_contract

    await salary_contract(session, person.id, club.id, "player", actor.at)
    row.status, row.signed_at = "signed", actor.at
    await session.flush()
    await user_effect(
        session,
        actor,
        "academy_candidate_signed",
        Surface.CLUB,
        ownership=ownership,
        club_id=club.id,
    )
    return person.id


async def _youth_variants(session: AsyncSession) -> tuple[str, ...]:
    catalog = load_whole_catalog()
    approved = [
        key
        for key, entry in catalog["variants"].items()
        if entry["bank"] == "players" and 17 <= entry.get("depicted_age", 0) <= 21
    ]
    variants = tuple(
        (
            await session.scalars(
                select(AppearanceVariant.id)
                .join(AppearanceIdentity, AppearanceIdentity.id == AppearanceVariant.identity_id)
                .where(
                    AppearanceIdentity.origin_bank == "players",
                    AppearanceVariant.bank == "players",
                    (AppearanceVariant.stage == "player-youth")
                    | AppearanceVariant.id.in_(approved),
                )
            )
        ).all()
    )
    if not variants:
        raise AppearanceExhausted("No approved youth portrait in the reusable catalog")
    return variants
