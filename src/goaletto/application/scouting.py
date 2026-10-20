"""Paid private searches. Caller owns transaction; no clocks or Telegram I/O."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.candidates import lifecycle_event
from goaletto.application.finances import salary_contract
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.application.market import active_count, calendar_for, incoming_count, owner_valid
from goaletto.application.match_runtime import NotReady
from goaletto.application.money import debit_locked, locked_accounts
from goaletto.application.persons import NewPerson, create_person
from goaletto.domain.finances import POLICY_VERSION as ECONOMY
from goaletto.domain.finances import PRIMARY, player_wage, scout_mission_fee, scout_signing_fee
from goaletto.domain.person_names import person_name
from goaletto.domain.scouting import POLICY, discovery, findings_count, potential_range
from goaletto.domain.training import POLICY_VERSION as TRAINING
from goaletto.domain.training import PROGRESS_KEYS
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    Fixture,
    Job,
    Manager,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    Player,
    PlayerDevelopment,
    ScoutDiscovery,
    ScoutMission,
    ScoutSigning,
    Staff,
    StaffQualification,
)
from goaletto.portraits.whole import load_whole_catalog


@dataclass(frozen=True)
class Finding:
    offer: ScoutDiscovery
    person: Person
    player: Player


@dataclass(frozen=True)
class ScoutScreen:
    ownership_id: UUID
    scout: Person | None
    skill: int
    cycle_rate: int
    money: int
    missions: tuple[ScoutMission, ...]
    findings: tuple[Finding, ...]


async def employed_scout(session: AsyncSession, club_id: UUID) -> tuple[Person, int] | None:
    row = (
        await session.execute(
            select(Person, StaffQualification.skill)
            .select_from(Person)
            .join(Staff, Staff.person_id == Person.id)
            .join(
                StaffQualification,
                (StaffQualification.person_id == Staff.person_id)
                & (StaffQualification.role == "scout"),
            )
            .where(
                Staff.club_id == club_id,
                Staff.role == "scout",
                Staff.status.in_(("active", "last_season")),
            )
        )
    ).first()
    return (row[0], row[1]) if row else None


async def mission_valid(session: AsyncSession, mission: ScoutMission, at: datetime) -> bool:
    scout = await session.get(Staff, mission.scout_id, populate_existing=True)
    return bool(
        await owner_valid(session, mission.ownership_id, mission.club_id, at)
        and scout
        and scout.club_id == mission.club_id
        and scout.role == "scout"
        and scout.status in {"active", "last_season"}
    )


async def cancel_invalid(session: AsyncSession, club_id: UUID, at: datetime) -> None:
    missions = (
        await session.scalars(
            select(ScoutMission)
            .where(ScoutMission.club_id == club_id, ScoutMission.status == "active")
            .with_for_update()
        )
    ).all()
    for mission in missions:
        if not await mission_valid(session, mission, at):
            mission.status, mission.finished_at = "cancelled", at
    await session.flush()


async def load_scout(session: AsyncSession, actor: Actor, *, record: bool = True) -> ScoutScreen:
    owner, club = await owned_club(session, actor)
    await allocation_lock(session)
    await cancel_invalid(session, club.id, actor.at)
    scout = await employed_scout(session, club.id)
    from goaletto.infrastructure.models import SalaryContract

    rate = (
        await session.scalar(
            select(SalaryContract.weekly_rate).where(
                SalaryContract.person_id == scout[0].id, SalaryContract.ended_at.is_(None)
            )
        )
        if scout
        else 0
    )
    missions = tuple(
        (
            await session.scalars(
                select(ScoutMission)
                .where(
                    ScoutMission.club_id == club.id,
                    ScoutMission.ownership_id == owner.id,
                    (ScoutMission.status == "active")
                    | ((ScoutMission.status == "completed") & (ScoutMission.expires_at > actor.at)),
                )
                .order_by(ScoutMission.started_at.desc())
            )
        ).all()
    )
    ids = [
        m.id
        for m in missions
        if m.status == "completed" and m.expires_at and actor.at < m.expires_at
    ]
    rows = (
        await session.execute(
            select(ScoutDiscovery, Person, Player)
            .join(Person, Person.id == ScoutDiscovery.person_id)
            .join(Player, Player.person_id == Person.id)
            .where(
                ScoutDiscovery.mission_id.in_(ids),
                ScoutDiscovery.signed_at.is_(None),
                ScoutDiscovery.declined_at.is_(None),
            )
            .order_by(ScoutDiscovery.mission_id, ScoutDiscovery.rank)
        )
    ).all()
    if record:
        await user_effect(
            session, actor, "club_opened", Surface.CLUB, ownership=owner, club_id=club.id
        )
    account = (await locked_accounts(session, club.id))[club.id]
    return ScoutScreen(
        owner.id,
        scout[0] if scout else None,
        scout[1] if scout else 0,
        rate or 0,
        account.available,
        missions,
        tuple(Finding(*r) for r in rows),
    )


async def start_search(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    scout_id: UUID,
    position: str,
    days: int,
    quoted_fee: int,
) -> ScoutMission:
    owner, club = await owned_club(session, actor)
    if owner.id != ownership_id:
        raise PermissionDenied("Old owner")
    await allocation_lock(session)
    prior = await session.scalar(
        select(ScoutMission).where(ScoutMission.operation_id == actor.operation_id)
    )
    if prior:
        if (
            prior.club_id,
            prior.ownership_id,
            prior.scout_id,
            prior.position,
            prior.days,
            prior.fee,
        ) != (club.id, owner.id, scout_id, position, days, quoted_fee):
            raise ValueError("Search operation reused")
        return prior
    await cancel_invalid(session, club.id, actor.at)
    scout = await employed_scout(session, club.id)
    if scout is None or scout[0].id != scout_id or position not in PRIMARY:
        raise ValueError("Scout or position unavailable")
    fee = scout_mission_fee(scout[1], days)
    if quoted_fee != fee or type(quoted_fee) is not int:
        raise ValueError("Search quote changed")
    if await session.scalar(
        select(ScoutMission.id).where(
            ScoutMission.club_id == club.id, ScoutMission.status == "active"
        )
    ):
        raise ValueError("Scout is already searching")
    mission_id = uuid5(club.id, f"{POLICY}:{actor.operation_id}")
    await debit_locked(
        session,
        (await locked_accounts(session, club.id))[club.id],
        f"scout:{mission_id}",
        "scout_search",
        fee,
        actor.at,
        details={
            "mission_id": str(mission_id),
            "position": position,
            "days": days,
            "scout_id": str(scout_id),
            "policy": ECONOMY,
        },
    )
    mission = ScoutMission(
        id=mission_id,
        operation_id=actor.operation_id,
        club_id=club.id,
        ownership_id=owner.id,
        scout_id=scout_id,
        skill=scout[1],
        position=position,
        days=days,
        fee=fee,
        policy_version=POLICY,
        economy_policy=ECONOMY,
        started_at=actor.at,
        due_at=actor.at + timedelta(days=days),
        status="active",
    )
    session.add(mission)
    await session.flush()
    session.add(
        Job(
            id=uuid5(mission.id, "complete"),
            dedupe_key=f"scout:{mission.id}:complete",
            kind="scout.complete",
            payload={"version": 1, "mission_id": str(mission.id)},
            due_at=mission.due_at,
            status="pending",
            attempts=0,
        )
    )
    await user_effect(
        session, actor, "scout_search_started", Surface.CLUB, ownership=owner, club_id=club.id
    )
    return mission


async def cancel_search(
    session: AsyncSession, actor: Actor, ownership_id: UUID, mission_id: UUID
) -> None:
    owner, club = await owned_club(session, actor)
    await allocation_lock(session)
    mission = await session.get(ScoutMission, mission_id, with_for_update=True)
    if (
        owner.id != ownership_id
        or mission is None
        or mission.ownership_id != owner.id
        or mission.club_id != club.id
    ):
        raise PermissionDenied("Old search")
    if mission.status == "cancelled":
        return
    if mission.status != "active":
        raise ValueError("Search already finished")
    mission.status, mission.finished_at = "cancelled", actor.at
    await user_effect(
        session, actor, "scout_search_cancelled", Surface.CLUB, ownership=owner, club_id=club.id
    )


async def complete_search(session: AsyncSession, job: Job, at: datetime) -> None:
    require_utc(at)
    if (
        job.kind not in {"scout.complete", "scout.expire"}
        or set(job.payload) != {"version", "mission_id"}
        or type(job.payload["version"]) is not int
        or job.payload["version"] != 1
    ):
        raise ValueError("Invalid scout job")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Busy scout allocation")
    mission = await session.get(ScoutMission, UUID(job.payload["mission_id"]), with_for_update=True)
    if mission is None or mission.policy_version != POLICY or mission.economy_policy != ECONOMY:
        raise ValueError("Unknown search")
    if job.kind == "scout.expire":
        if mission.expires_at is None or job.due_at != mission.expires_at:
            raise ValueError("Invalid report expiry")
        if at < mission.expires_at:
            raise NotReady("Report still valid")
        if mission.status == "completed":
            mission.status = "expired"
        return
    if job.due_at != mission.due_at:
        raise ValueError("Invalid search deadline")
    if mission.status != "active":
        return
    if at < mission.due_at:
        raise NotReady("Search has not finished")
    if not await mission_valid(session, mission, at):
        mission.status, mission.finished_at = "cancelled", at
        return
    season, window = await calendar_for(session, mission.club_id, at)
    # At least eight days to decide, and always include the next transfer window.
    report_expires = max(at + timedelta(days=8), window.closes_at)
    catalog = load_whole_catalog()
    eligible = tuple(
        k
        for k, v in catalog["variants"].items()
        if v["bank"] == "players"
        and (v.get("stage") == "player-youth" or 17 <= v.get("depicted_age", 0) <= 21)
    )
    for rank in range(1, findings_count(mission.days) + 1):
        person_id = uuid5(mission.id, f"finding:{rank}")
        profile = discovery(person_id, mission.position, mission.skill)
        person = await create_person(
            session,
            NewPerson(
                id=person_id,
                creation_key=f"{POLICY}:{mission.id}:{rank}",
                name=person_name(person_id),
                age=profile.age,
                career="player",
                origin_bank="players",
                eligible_variant_ids=eligible,
                home_league_set_id=season.league_set_id,
            ),
        )
        person.age_applied_for_season_id = season.id
        session.add(
            Player(
                person_id=person.id,
                position=mission.position,
                alternative_positions=list(profile.alternatives),
                **dict(zip(SKILL_NAMES, profile.skills, strict=True)),
                form=6,
                experience=0,
                morale=50,
                status="active",
            )
        )
        session.add(
            PlayerDevelopment(
                person_id=person.id,
                caps=profile.caps,
                progress=dict.fromkeys(PROGRESS_KEYS, 0),
                policy_version=TRAINING,
            )
        )
        session.add(lifecycle_event(person, season, "age_applied", at))
        session.add(lifecycle_event(person, season, "player_last_decision", at))
        await session.flush()
        low, high = potential_range(
            profile.caps[SKILL_NAMES[PRIMARY[mission.position]]], mission.skill
        )
        session.add(
            ScoutDiscovery(
                person_id=person.id,
                mission_id=mission.id,
                rank=rank,
                fee=scout_signing_fee(
                    profile.skills[:8], profile.age, mission.position, 1 + len(profile.alternatives)
                ),
                cycle_rate=player_wage(
                    profile.skills[:8], profile.age, mission.position, 1 + len(profile.alternatives)
                ),
                potential_low=low,
                potential_high=high,
            )
        )
        await session.flush()  # Private supply fence exists before any replenishment can run.
    mission.status, mission.finished_at, mission.expires_at = "completed", at, report_expires
    session.add(
        Job(
            id=uuid5(mission.id, "expire"),
            dedupe_key=f"scout:{mission.id}:expire",
            kind="scout.expire",
            payload={"version": 1, "mission_id": str(mission.id)},
            due_at=mission.expires_at,
            status="pending",
            attempts=0,
        )
    )
    owner = await session.get(OwnershipPeriod, mission.ownership_id)
    manager = await session.get(Manager, owner.manager_id) if owner else None
    if manager:
        session.add(
            OutboxMessage(
                id=uuid5(mission.id, "report"),
                dedupe_key=f"scout:{mission.id}:report",
                chat_id=manager.telegram_id,
                due_at=at,
                status="pending",
                attempts=0,
                priority=70,
                payload={
                    "version": 1,
                    "kind": "scout_report",
                    "ownership_id": str(mission.ownership_id),
                    "club_id": str(mission.club_id),
                    "mission_id": str(mission.id),
                    "count": findings_count(mission.days),
                },
            )
        )


async def finding_for(
    session: AsyncSession, actor: Actor, ownership_id: UUID, person_id: UUID
) -> Finding:
    owner, club = await owned_club(session, actor)
    offer = await session.get(ScoutDiscovery, person_id, with_for_update=True)
    mission = await session.get(ScoutMission, offer.mission_id) if offer else None
    if (
        owner.id != ownership_id
        or offer is None
        or mission is None
        or mission.club_id != club.id
        or mission.ownership_id != owner.id
        or mission.status != "completed"
        or mission.expires_at is None
        or actor.at >= mission.expires_at
        or offer.signed_at
        or offer.declined_at
    ):
        raise PermissionDenied("Finding unavailable")
    player, person = await session.get(Player, person_id), await session.get(Person, person_id)
    if (
        player is None
        or person is None
        or player.club_id
        or player.status != "active"
        or person.career != "player"
    ):
        raise ValueError("Finding unavailable")
    return Finding(offer, person, player)


async def sign_finding(
    session: AsyncSession, actor: Actor, ownership_id: UUID, person_id: UUID, quoted_fee: int
) -> ScoutSigning:
    await allocation_lock(session)
    owner, club = await owned_club(session, actor)
    if owner.id != ownership_id:
        raise PermissionDenied("Old owner")
    prior = await session.get(ScoutSigning, actor.operation_id)
    if prior:
        if (prior.person_id, prior.ownership_id, prior.club_id, prior.fee) != (
            person_id,
            owner.id,
            club.id,
            quoted_fee,
        ):
            raise ValueError("Signing operation reused")
        return prior
    finding = await finding_for(session, actor, ownership_id, person_id)
    offer, player, person = finding.offer, finding.player, finding.person
    if quoted_fee != offer.fee:
        raise ValueError("Finding quote changed")
    season, window = await calendar_for(session, club.id, actor.at)
    if (
        not window.contains(actor.at)
        or await active_count(session, club.id) + await incoming_count(session, club.id) >= 25
    ):
        raise ValueError("Transfer window or roster capacity unavailable")
    if await session.scalar(
        select(Fixture.id)
        .where(
            (Fixture.home_club_id == club.id) | (Fixture.away_club_id == club.id),
            Fixture.lineup_lock_at <= actor.at,
            Fixture.status.in_(("scheduled", "live", "paused")),
            Fixture.actual_finish_at.is_(None),
        )
        .limit(1)
    ):
        raise ValueError("Wait until the locked fixture finishes")
    mission = await session.get(ScoutMission, offer.mission_id)
    if mission is None:
        raise ValueError("Missing search")
    await debit_locked(
        session,
        (await locked_accounts(session, club.id))[club.id],
        f"scout-sign:{actor.operation_id}",
        "signing_fee",
        offer.fee,
        actor.at,
        details={
            "person_id": str(person.id),
            "mission_id": str(mission.id),
            "policy": mission.economy_policy,
            "cycle_rate": offer.cycle_rate,
        },
    )
    player.club_id = club.id
    person.home_league_set_id, person.age_applied_for_season_id = season.league_set_id, season.id
    offer.signed_at = actor.at
    await session.flush()
    await salary_contract(
        session,
        person.id,
        club.id,
        "player",
        actor.at,
        quoted_player_wage=offer.cycle_rate,
        policy_version=mission.economy_policy,
    )
    receipt = ScoutSigning(
        operation_id=actor.operation_id,
        person_id=person.id,
        club_id=club.id,
        ownership_id=owner.id,
        fee=offer.fee,
        cycle_rate=offer.cycle_rate,
        economy_policy=mission.economy_policy,
        signed_at=actor.at,
    )
    session.add(receipt)
    await user_effect(
        session, actor, "candidate_signed", Surface.CLUB, ownership=owner, club_id=club.id
    )
    await session.flush()
    return receipt


async def decline_finding(
    session: AsyncSession, actor: Actor, ownership_id: UUID, person_id: UUID
) -> None:
    await allocation_lock(session)
    finding = await finding_for(session, actor, ownership_id, person_id)
    finding.offer.declined_at = actor.at
    owner, club = await owned_club(session, actor)
    await user_effect(session, actor, "club_opened", Surface.CLUB, ownership=owner, club_id=club.id)
