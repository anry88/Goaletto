"""Global bounded candidate supply. No commits, image generation or network calls."""

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.finances import salary_contract
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.application.market import (
    active_count,
    calendar_for,
    incoming_count,
    league_set_for,
    owner_valid,
)
from goaletto.application.money import debit_locked, locked_accounts
from goaletto.application.persons import NewPerson, create_person
from goaletto.domain.candidates import (
    PLAYER_POSITIONS,
    POOL_POLICY,
    STAFF_ROLES,
    candidate_profile,
    deficit,
)
from goaletto.domain.retirement import player_last_season, staff_last_season
from goaletto.domain.training import POLICY_VERSION, PROGRESS_KEYS, SKILLS, initial_caps
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    Auction,
    CandidateOffer,
    CandidatePool,
    CandidateSigning,
    Fixture,
    Person,
    PersonSeasonEvent,
    Player,
    PlayerDevelopment,
    Season,
    Staff,
    StaffQualification,
    WorldSeedReceipt,
)
from goaletto.portraits.whole import load_whole_catalog

POOL_NAMESPACE = UUID("a493418f-576b-4e55-988c-74891f207028")


@dataclass(frozen=True)
class CandidateItem:
    offer: CandidateOffer
    person: Person
    skills: tuple[int, ...]


@dataclass(frozen=True)
class CandidateScreen:
    ownership_id: UUID
    club_name: str
    available_money: int
    items: tuple[CandidateItem, ...]
    employed: tuple[tuple[str, str, int, int, str], ...]


async def pool_calendar(session: AsyncSession) -> Season:
    # Global candidates use the first world's calendar until employed. It does not
    # scope availability: managers from every set share the same offers.
    home_id = await session.scalar(
        select(CandidatePool.home_league_set_id)
        .order_by(CandidatePool.kind, CandidatePool.profile)
        .limit(1)
    )
    if home_id is None:
        home_id = await session.scalar(
            select(WorldSeedReceipt.league_set_id)
            .order_by(WorldSeedReceipt.created_at, WorldSeedReceipt.key)
            .limit(1)
        )
    if home_id is None:
        raise ValueError("Candidate supply requires an initialized game")
    current = await session.scalar(
        select(Season)
        .where(
            Season.league_set_id == home_id,
            Season.status.in_(("scheduled", "active", "finished")),
        )
        .order_by(Season.number.desc())
        .limit(1)
    )
    if current is None:
        raise ValueError("Global candidate calendar missing")
    return current


async def supply_count(session: AsyncSession, kind: str, profile: str, at: datetime) -> int:
    if kind == "player":
        free = (
            await session.scalar(
                select(func.count())
                .select_from(Player)
                .join(Person)
                .where(
                    Player.club_id.is_(None), Player.status == "active", Person.career == "player"
                )
            )
            or 0
        )
        listed = (
            await session.scalar(
                select(func.count())
                .select_from(Auction)
                .join(Player, Player.person_id == Auction.person_id)
                .join(Person)
                .where(
                    Auction.status == "open",
                    Auction.ends_at > at,
                    Player.status == "active",
                    Person.career == "player",
                )
            )
            or 0
        )
        return free + listed
    return (
        await session.scalar(
            select(func.count())
            .select_from(Staff)
            .join(Person)
            .join(StaffQualification, StaffQualification.person_id == Staff.person_id)
            .where(
                Staff.club_id.is_(None),
                Staff.status == "active",
                Person.career == "staff",
                StaffQualification.role == profile,
            )
        )
        or 0
    )


def lifecycle_event(
    person: Person, season: Season, stage: str, at: datetime, *, staff: bool = False
) -> PersonSeasonEvent:
    decision = (
        staff_last_season(person.age, 9999) if staff else player_last_season(person.age, 9999)
    )
    return PersonSeasonEvent(
        id=uuid5(person.id, f"{POOL_POLICY}:{season.id}:{stage}"),
        person_id=person.id,
        season_id=season.id,
        stage=stage,
        age=person.age,
        policy_version=decision.policy_version if stage.endswith("decision") else POOL_POLICY,
        probability_bps=decision.probability_bps if stage.endswith("decision") else None,
        draw=decision.draw if stage.endswith("decision") else None,
        is_last_season=decision.is_last_season if stage.endswith("decision") else None,
        occurred_at=at,
    )


async def _former_player(
    session: AsyncSession, role: str, season: Season, at: datetime, seed_id: UUID
) -> Person | None:
    if role == "physiotherapist":
        return None
    positions = {
        "goalkeeping_coach": ("GK",),
        "defence_coach": ("DL", "DC", "DR", "DM"),
        "attack_coach": ("FC", "AMC", "AML", "AMR", "ML", "MR"),
    }
    query = (
        select(Person)
        .join(Player)
        .where(
            Player.status == "retired",
            Person.career == "retired",
            Person.age < 60,
            ~select(Staff.person_id).where(Staff.person_id == Person.id).exists(),
            ~select(CandidateOffer.person_id)
            .where(CandidateOffer.person_id == Person.id, CandidateOffer.kind != "player")
            .exists(),
        )
    )
    if role in positions:
        query = query.where(Player.position.in_(positions[role]))
    person = await session.scalar(query.order_by(Person.id).with_for_update(of=Person).limit(1))
    if person is None:
        return None
    # One person and face across both careers; never borrow a different staff face.
    person.career = "staff"
    person.home_league_set_id = season.league_set_id
    if person.age_applied_for_season_id != season.id:
        person.age += 1
        person.age_applied_for_season_id = season.id
        prior = await session.scalar(
            select(PersonSeasonEvent.id).where(
                PersonSeasonEvent.person_id == person.id,
                PersonSeasonEvent.season_id == season.id,
                PersonSeasonEvent.stage == "age_applied",
            )
        )
        if prior is None:
            session.add(lifecycle_event(person, season, "age_applied", at, staff=True))
    session.add(Staff(person_id=person.id, status="active"))
    await session.flush()
    _, _, skills, _, _ = candidate_profile(seed_id, "staff", role)
    session.add(
        StaffQualification(
            person_id=person.id, role=role, skill=skills[0], qualified_for_season_id=season.id
        )
    )
    session.add(lifecycle_event(person, season, "staff_last_decision", at, staff=True))
    session.add(lifecycle_event(person, season, "career_assigned", at, staff=True))
    return person


async def replenish_pool(
    session: AsyncSession, kind: str, profile: str, at: datetime, *, limit: int | None = None
) -> int:
    """Generate only the deficit; after a sale/hire, limit=1 prevents batch growth."""
    require_utc(at)
    await allocation_lock(session)
    if (kind, profile) not in [("player", "player"), *[("staff", r) for r in STAFF_ROLES]]:
        raise ValueError("Unknown candidate pool")
    target = 12 if kind == "player" else 2
    season = await pool_calendar(session)
    await session.execute(
        insert(CandidatePool)
        .values(
            kind=kind,
            profile=profile,
            target=target,
            generation=0,
            home_league_set_id=season.league_set_id,
        )
        .on_conflict_do_nothing(index_elements=["kind", "profile"])
    )
    pool = await session.scalar(
        select(CandidatePool)
        .where(CandidatePool.kind == kind, CandidatePool.profile == profile)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if pool is None or pool.target != target:
        raise ValueError("Invalid supply policy")
    await _adopt_free_people(session, kind, profile, at)
    missing = deficit(await supply_count(session, kind, profile, at), target, limit)
    if not missing:
        return 0  # No portrait assignment, randomness or person creation above the threshold.
    bank = "players" if kind == "player" else "staff"
    catalog = load_whole_catalog()
    stage = "player-youth" if kind == "player" else "staff-middle"
    eligible = tuple(
        key
        for key, entry in catalog["variants"].items()
        if entry["bank"] == bank
        and (
            entry.get("stage") == stage
            or (
                17 <= entry.get("depicted_age", 0) <= 21
                if kind == "player"
                else 45 <= entry.get("depicted_age", 0) <= 59
            )
        )
    )
    for _ in range(missing):
        pool.generation += 1
        person_id = uuid5(POOL_NAMESPACE, f"{POOL_POLICY}:{kind}:{profile}:{pool.generation}")
        selected = (
            PLAYER_POSITIONS[(pool.generation - 1) % len(PLAYER_POSITIONS)]
            if kind == "player"
            else profile
        )
        name, age, skills, fee, wage = candidate_profile(person_id, kind, selected)
        person = (
            await _former_player(session, profile, season, at, person_id)
            if kind == "staff"
            else None
        )
        if person is None:
            person = await create_person(
                session,
                NewPerson(
                    id=person_id,
                    creation_key=f"{POOL_POLICY}:{kind}:{profile}:{pool.generation}",
                    name=name,
                    age=age,
                    career=kind,
                    origin_bank=bank,
                    eligible_variant_ids=eligible,
                    home_league_set_id=season.league_set_id,
                ),
            )
            person.age_applied_for_season_id = season.id
            if kind == "player":
                session.add(
                    Player(
                        person_id=person.id,
                        position=selected,
                        **dict(zip(SKILL_NAMES, skills, strict=True)),
                        form=6,
                        experience=0,
                        morale=50,
                        status="active",
                    )
                )
                session.add(
                    PlayerDevelopment(
                        person_id=person.id,
                        caps=initial_caps(person.id, dict(zip(SKILLS, skills[:8], strict=True))),
                        progress=dict.fromkeys(PROGRESS_KEYS, 0),
                        policy_version=POLICY_VERSION,
                    )
                )
            else:
                session.add(Staff(person_id=person.id, status="active"))
            await session.flush()
            if kind == "staff":
                session.add(
                    StaffQualification(
                        person_id=person.id,
                        role=profile,
                        skill=skills[0],
                        qualified_for_season_id=season.id,
                    )
                )
            session.add(lifecycle_event(person, season, "age_applied", at, staff=kind == "staff"))
            session.add(
                lifecycle_event(
                    person,
                    season,
                    "player_last_decision" if kind == "player" else "staff_last_decision",
                    at,
                    staff=kind == "staff",
                )
            )
        offer = await session.get(CandidateOffer, person.id)
        if offer is None:
            session.add(
                CandidateOffer(
                    person_id=person.id,
                    kind=kind,
                    pool_profile=profile,
                    profile=selected,
                    fee=fee,
                    weekly_rate=wage,
                    created_at=at,
                    status="available",
                )
            )
        else:
            if offer.kind != "player" or kind != "staff" or offer.status == "available":
                raise ValueError("Candidate career offer cannot be overwritten")
            # The player receipt retains its old role/fee/wage; this is the same human.
            offer.kind, offer.pool_profile, offer.profile = kind, profile, selected
            offer.fee, offer.weekly_rate, offer.created_at, offer.status = (
                fee,
                wage,
                at,
                "available",
            )
        await session.flush()
    return missing


async def load_candidates(
    session: AsyncSession,
    actor: Actor,
    kind: str,
    *,
    record: bool = True,
    refresh_supply: bool = True,
) -> CandidateScreen:
    owner, club = await owned_club(session, actor)
    if kind not in ("player", "staff"):
        raise ValueError("Unknown candidate kind")
    if refresh_supply:
        for profile in ("player",) if kind == "player" else STAFF_ROLES:
            await replenish_pool(session, kind, profile, actor.at)
    account = (await locked_accounts(session, club.id))[club.id]
    rows = (
        await session.execute(
            select(CandidateOffer, Person)
            .join(Person)
            .where(
                CandidateOffer.kind == kind,
                CandidateOffer.status == "available",
                Person.career == kind,
            )
            .order_by(CandidateOffer.profile, Person.id)
        )
    ).all()
    items = []
    for offer, person in rows:
        worker = cast(
            Player | Staff | None,
            await session.get(Player if kind == "player" else Staff, person.id),
        )
        if worker is None or worker.club_id is not None or worker.status != "active":
            continue
        skill_values = (
            tuple(getattr(worker, k) for k in SKILL_NAMES[:8])
            if kind == "player"
            else (
                await session.scalar(
                    select(StaffQualification.skill).where(
                        StaffQualification.person_id == person.id,
                        StaffQualification.role == offer.profile,
                    )
                )
                or 0,
            )
        )
        items.append(CandidateItem(offer, person, skill_values))
    employed = (
        await session.execute(
            select(Staff.role, Person.name, Person.age, StaffQualification.skill, Staff.status)
            .join(Person)
            .join(
                StaffQualification,
                (StaffQualification.person_id == Staff.person_id)
                & (StaffQualification.role == Staff.role),
            )
            .where(Staff.club_id == club.id, Staff.status.in_(("active", "last_season")))
            .order_by(Staff.role)
        )
    ).all()
    if record:
        await user_effect(
            session, actor, "market_opened", Surface.CLUB, ownership=owner, club_id=club.id
        )
    return CandidateScreen(
        owner.id,
        club.name,
        account.available,
        tuple(items),
        tuple(
            (cast(str, role), name, age, skill, status)
            for role, name, age, skill, status in employed
        ),
    )


async def sign_candidate(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    person_id: UUID,
    quoted_fee: int,
    *,
    at: datetime,
) -> CandidateSigning:
    require_utc(at)
    if at < actor.at:
        raise ValueError("Signing cannot predate user action")
    owner, club = await owned_club(session, actor)
    if owner.id != ownership_id or not await owner_valid(session, owner.id, club.id, at):
        raise PermissionDenied("Current owner required")
    prior = await session.get(CandidateSigning, actor.operation_id)
    if prior is not None:
        if (prior.person_id, prior.ownership_id, prior.club_id, prior.fee) != (
            person_id,
            owner.id,
            club.id,
            quoted_fee,
        ):
            raise ValueError("Signing operation reused")
        return prior
    offer = await session.scalar(
        select(CandidateOffer)
        .where(CandidateOffer.person_id == person_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    person = await session.get(Person, person_id, populate_existing=True)
    if offer is None or person is None or offer.status != "available" or offer.fee != quoted_fee:
        raise ValueError("Offer unavailable or price changed")
    worker = await session.scalar(
        select(Player if offer.kind == "player" else Staff)
        .where((Player.person_id if offer.kind == "player" else Staff.person_id) == person.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    worker = cast(Player | Staff | None, worker)
    if (
        worker is None
        or worker.club_id is not None
        or worker.status != "active"
        or person.career != offer.kind
    ):
        raise ValueError("Candidate unavailable")
    if offer.created_at > at:
        raise ValueError("Signing predates the offer")
    if isinstance(worker, Staff):
        if await session.get(StaffQualification, (person.id, offer.profile)) is None:
            raise ValueError("Candidate lacks the required qualification")
    elif worker.position != offer.profile:
        raise ValueError("Player offer differs from the profile")
    season, window = await calendar_for(session, club.id, at)
    if offer.kind == "player":
        if (
            not window.contains(at)
            or await active_count(session, club.id) + await incoming_count(session, club.id) >= 25
        ):
            raise ValueError("Transfer window or roster capacity unavailable")
        busy = await session.scalar(
            select(Fixture.id)
            .where(
                (Fixture.home_club_id == club.id) | (Fixture.away_club_id == club.id),
                Fixture.lineup_lock_at <= at,
                Fixture.status.in_(("scheduled", "live", "paused")),
                Fixture.actual_finish_at.is_(None),
            )
            .limit(1)
        )
        if busy is not None:
            raise ValueError("Wait until the locked fixture finishes")
    elif await session.scalar(
        select(Staff.person_id)
        .where(
            Staff.club_id == club.id,
            Staff.role == offer.profile,
            Staff.status.in_(("active", "last_season")),
        )
        .limit(1)
    ):
        raise ValueError("Staff role already employed")
    account = (await locked_accounts(session, club.id))[club.id]
    await debit_locked(
        session,
        account,
        f"signing:{actor.operation_id}",
        "signing_fee",
        offer.fee,
        at,
        details={"person_id": str(person.id), "kind": offer.kind, "role": offer.profile},
    )
    worker.club_id = club.id
    if isinstance(worker, Staff):
        worker.role = offer.profile
    person.home_league_set_id = await league_set_for(session, club)
    # Changing calendars never adds a year at signing; the new club owns subsequent aging.
    person.age_applied_for_season_id = season.id
    offer.status = "signed"
    await session.flush()
    await salary_contract(
        session,
        person.id,
        club.id,
        "player" if offer.kind == "player" else offer.profile,
        at,
        quoted_player_wage=offer.weekly_rate if offer.kind == "player" else None,
    )
    receipt = CandidateSigning(
        operation_id=actor.operation_id,
        person_id=person.id,
        kind=offer.kind,
        profile=offer.profile,
        club_id=club.id,
        ownership_id=owner.id,
        fee=offer.fee,
        weekly_rate=offer.weekly_rate,
        signed_at=at,
    )
    session.add(receipt)
    await user_effect(
        session, actor, "candidate_signed", Surface.CLUB, ownership=owner, club_id=club.id
    )
    await session.flush()
    await replenish_pool(session, offer.kind, offer.pool_profile, at, limit=1)
    return receipt


async def _adopt_free_people(session: AsyncSession, kind: str, profile: str, at: datetime) -> None:
    from goaletto.domain.finances import player_wage, staff_wage

    missing_offer = (
        ~select(CandidateOffer.person_id).where(CandidateOffer.person_id == Person.id).exists()
    )
    if kind == "player":
        rows = (
            await session.execute(
                select(Player, Person)
                .join(Person)
                .where(
                    Player.club_id.is_(None),
                    Player.status == "active",
                    Person.career == "player",
                    missing_offer,
                )
                .order_by(Person.id)
            )
        ).all()
        for worker, person in rows:
            rate = player_wage(tuple(getattr(worker, k) for k in SKILL_NAMES[:8]))
            session.add(
                CandidateOffer(
                    person_id=person.id,
                    kind=kind,
                    pool_profile=profile,
                    profile=worker.position,
                    status="available",
                    fee=100000,
                    weekly_rate=rate,
                    created_at=at,
                )
            )
    else:
        people = (
            await session.scalars(
                select(Person)
                .join(Staff)
                .join(StaffQualification)
                .where(
                    Staff.club_id.is_(None),
                    Staff.status == "active",
                    Person.career == "staff",
                    StaffQualification.role == profile,
                    missing_offer,
                )
                .order_by(Person.id)
            )
        ).all()
        for person in people:
            rate = staff_wage(profile)
            session.add(
                CandidateOffer(
                    person_id=person.id,
                    kind=kind,
                    pool_profile=profile,
                    profile=profile,
                    status="available",
                    fee=4 * rate,
                    weekly_rate=rate,
                    created_at=at,
                )
            )
    await session.flush()
