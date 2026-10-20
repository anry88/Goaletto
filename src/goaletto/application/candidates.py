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
from goaletto.domain.finances import POLICY_VERSION as ECONOMY_POLICY
from goaletto.domain.market_players import market_player
from goaletto.domain.nationalities import flag_name
from goaletto.domain.retirement import player_last_season, staff_last_season
from goaletto.domain.training import POLICY_VERSION, PROGRESS_KEYS
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
    ScoutDiscovery,
    Season,
    Staff,
    StaffCareer,
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
    alternatives: tuple[str, ...] = ()
    wage: int | None = None
    loyal: bool = False
    private_until: datetime | None = None


@dataclass(frozen=True)
class CandidateScreen:
    ownership_id: UUID
    club_name: str
    available_money: int
    items: tuple[CandidateItem, ...]
    employed: tuple[tuple[str, str, int, int, str], ...]
    employee_ids: tuple[UUID, ...] = ()


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
                    Player.club_id.is_(None),
                    Player.status == "active",
                    Person.career == "player",
                    ~select(ScoutDiscovery.person_id)
                    .where(
                        ScoutDiscovery.person_id == Player.person_id,
                        ScoutDiscovery.signed_at.is_(None),
                    )
                    .exists(),
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
                Staff.status.in_(("active", "last_season")),
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
    stage = "player-adult" if kind == "player" else "staff-middle"
    eligible = tuple(
        key
        for key, entry in catalog["variants"].items()
        if entry["bank"] == bank
        and (
            entry.get("stage") == stage
            or (
                26 <= entry.get("depicted_age", 0) <= 30
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
                    alternative_positions=list(market_player(person.id, selected).alternatives),
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
                    caps=market_player(person.id, selected).caps,
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
        if kind == "staff":
            from goaletto.domain.finances import staff_fee, staff_wage

            qualification = await session.get(StaffQualification, (person.id, profile))
            if qualification is None:
                raise ValueError("Missing candidate qualification")
            fee = staff_fee(profile, qualification.skill, person.age)
            wage = staff_wage(profile, qualification.skill, person.age)
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
                    economy_policy=ECONOMY_POLICY,
                    created_at=at,
                    status="available",
                )
            )
        else:
            if offer.kind != "player" or kind != "staff" or offer.status == "available":
                raise ValueError("Candidate career offer cannot be overwritten")
            # The player receipt retains its old role/fee/wage; this is the same human.
            offer.kind, offer.pool_profile, offer.profile = kind, profile, selected
            offer.economy_policy = ECONOMY_POLICY
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
    from goaletto.application.staff_career import expire_offers

    await expire_offers(session, actor.at)
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
                (CandidateOffer.status == "available")
                | (
                    (CandidateOffer.status == "private")
                    & CandidateOffer.person_id.in_(
                        select(StaffCareer.person_id).where(
                            StaffCareer.origin_club_id == club.id, StaffCareer.status == "pending"
                        )
                    )
                ),
                Person.career == kind,
            )
            .order_by(
                (CandidateOffer.status == "private").desc(), CandidateOffer.profile, Person.id
            )
        )
    ).all()
    items = []
    for offer, person in rows:
        worker = cast(
            Player | Staff | None,
            await session.get(Player if kind == "player" else Staff, person.id),
        )
        if (
            worker is None
            or worker.club_id is not None
            or worker.status not in (("active", "last_season") if kind == "staff" else ("active",))
        ):
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
        from goaletto.application.staff_career import quoted_wage

        wage, loyal = (
            await quoted_wage(session, offer, club.id, actor.at)
            if kind == "staff"
            else (offer.weekly_rate, False)
        )
        career = await session.get(StaffCareer, person.id) if offer.status == "private" else None
        items.append(
            CandidateItem(
                offer,
                person,
                skill_values,
                tuple(worker.alternative_positions) if isinstance(worker, Player) else (),
                wage,
                loyal,
                career.expires_at if career else None,
            )
        )
    employed = (
        await session.execute(
            select(
                Staff.role,
                Person.name,
                Person.age,
                StaffQualification.skill,
                Staff.status,
                Staff.person_id,
                Person.country_code,
            )
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
            (cast(str, role), flag_name(name, country), age, skill, status)
            for role, name, age, skill, status, _, country in employed
        ),
        tuple(row[5] for row in employed),
    )


async def sign_candidate(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    person_id: UUID,
    quoted_fee: int,
    *,
    at: datetime,
    expected_outgoing: UUID | None = None,
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
    if (
        offer is None
        or person is None
        or offer.status not in {"available", "private"}
        or offer.fee != quoted_fee
    ):
        raise ValueError("Offer unavailable or price changed")
    from goaletto.application.staff_career import quoted_wage

    wage, _ = (
        await quoted_wage(session, offer, club.id, at)
        if offer.kind == "staff"
        else (offer.weekly_rate, False)
    )
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
        or worker.status
        not in (("active", "last_season") if offer.kind == "staff" else ("active",))
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
    if (
        isinstance(worker, Staff)
        and worker.status == "last_season"
        and worker.last_season_id != season.id
    ):
        raise ValueError("Last-season staff require the same season calendar")
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
    else:
        employed = await session.scalar(
            select(Staff.person_id).where(
                Staff.club_id == club.id,
                Staff.role == offer.profile,
                Staff.status.in_(("active", "last_season")),
            )
        )
        if employed != expected_outgoing:
            raise ValueError("Staff employment changed; obtain a new confirmation")
        if employed is not None:
            from goaletto.application.staff_changes import release_staff

            await release_staff(session, employed, club.id, at, normalize=False)
    account = (await locked_accounts(session, club.id))[club.id]
    await debit_locked(
        session,
        account,
        f"signing:{actor.operation_id}",
        "signing_fee",
        offer.fee,
        at,
        details={
            "person_id": str(person.id),
            "kind": offer.kind,
            "role": offer.profile,
            "policy": offer.economy_policy,
            "cycle_rate": wage,
        },
    )
    worker.club_id = club.id
    if isinstance(worker, Staff):
        worker.role = offer.profile
        from goaletto.application.staff_changes import normalize_tactic

        await session.flush()
        await normalize_tactic(session, club.id)
    person.home_league_set_id = await league_set_for(session, club)
    # Changing calendars never adds a year at signing; the new club owns subsequent aging.
    person.age_applied_for_season_id = season.id
    career = await session.get(StaffCareer, person.id) if offer.kind == "staff" else None
    if career and career.status == "pending":
        career.status, career.resolved_at = "accepted", at
    offer.status = "signed"
    await session.flush()
    await salary_contract(
        session,
        person.id,
        club.id,
        "player" if offer.kind == "player" else offer.profile,
        at,
        quoted_player_wage=offer.weekly_rate if offer.kind == "player" else None,
        quoted_staff_wage=wage if offer.kind == "staff" else None,
        policy_version=offer.economy_policy,
        employment_id=actor.operation_id if offer.kind == "staff" else None,
    )
    receipt = CandidateSigning(
        operation_id=actor.operation_id,
        person_id=person.id,
        kind=offer.kind,
        profile=offer.profile,
        club_id=club.id,
        ownership_id=owner.id,
        fee=offer.fee,
        weekly_rate=wage,
        economy_policy=offer.economy_policy,
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
    from goaletto.domain.finances import player_fee, player_wage, staff_fee, staff_wage

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
                    ~select(ScoutDiscovery.person_id)
                    .where(
                        ScoutDiscovery.person_id == Player.person_id,
                        ScoutDiscovery.signed_at.is_(None),
                    )
                    .exists(),
                    Player.status == "active",
                    Person.career == "player",
                    missing_offer,
                )
                .order_by(Person.id)
            )
        ).all()
        for worker, person in rows:
            skills = tuple(getattr(worker, k) for k in SKILL_NAMES[:8])
            positions = 1 + len(worker.alternative_positions)
            rate = player_wage(skills, person.age, worker.position, positions)
            session.add(
                CandidateOffer(
                    person_id=person.id,
                    kind=kind,
                    pool_profile=profile,
                    profile=worker.position,
                    status="available",
                    fee=player_fee(skills, person.age, worker.position, positions),
                    weekly_rate=rate,
                    economy_policy=ECONOMY_POLICY,
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
                    Staff.status.in_(("active", "last_season")),
                    Person.career == "staff",
                    StaffQualification.role == profile,
                    missing_offer,
                )
                .order_by(Person.id)
            )
        ).all()
        for person in people:
            qualification = await session.get(StaffQualification, (person.id, profile))
            if qualification is None:
                raise ValueError("Missing free staff qualification")
            rate = staff_wage(profile, qualification.skill, person.age)
            session.add(
                CandidateOffer(
                    person_id=person.id,
                    kind=kind,
                    pool_profile=profile,
                    profile=profile,
                    status="available",
                    fee=staff_fee(profile, qualification.skill, person.age),
                    weekly_rate=rate,
                    economy_policy=ECONOMY_POLICY,
                    created_at=at,
                )
            )
    await session.flush()
