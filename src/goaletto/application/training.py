"""Durable daily choices and atomic development; no Telegram/network calls."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.application.match_runtime import NotReady
from goaletto.domain.game_cycle import midnight
from goaletto.domain.training import (
    POLICY_VERSION,
    PROGRESS_KEYS,
    SKILLS,
    Focus,
    Intensity,
    TrainingPlayer,
    coach_bonus,
    initial_caps,
    train_player,
)
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    FixturePlan,
    Job,
    LineupSlot,
    Manager,
    OwnershipPeriod,
    Person,
    Player,
    PlayerDevelopment,
    Staff,
    StaffQualification,
    TrainingDecision,
    TrainingPlan,
)


@dataclass(frozen=True)
class TrainingMember:
    id: UUID
    name: str
    position: str


@dataclass(frozen=True)
class TrainingScreen:
    club_name: str
    ownership_id: UUID
    week_start: datetime
    due_at: datetime
    plan: TrainingPlan | None
    last_result: TrainingPlan | None
    members: tuple[TrainingMember, ...]
    blocked: bool = False


def week_start(at: datetime) -> datetime:
    require_utc(at)
    return midnight(at.date())


async def training_blocked(session: AsyncSession, club_id: UUID, day: datetime) -> bool:
    fixture = await session.scalar(
        select(Fixture.id)
        .where(
            (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            Fixture.status != "cancelled",
            Fixture.scheduled_start_at >= day,
            Fixture.scheduled_start_at < day + timedelta(days=1),
        )
        .limit(1)
    )
    plan = await session.scalar(
        select(FixturePlan.id)
        .where(
            (FixturePlan.home_club_id == club_id) | (FixturePlan.away_club_id == club_id),
            FixturePlan.assigned_day == day.date(),
        )
        .limit(1)
    )
    return fixture is not None or plan is not None


async def load_training(
    session: AsyncSession, actor: Actor, *, record: bool = True
) -> TrainingScreen:
    ownership, club = await owned_club(session, actor)
    week = week_start(actor.at)
    plan = await session.scalar(
        select(TrainingPlan).where(
            TrainingPlan.club_id == club.id,
            TrainingPlan.week_start == week,
            TrainingPlan.policy_version == "training-daily-v1",
            TrainingPlan.ownership_id == ownership.id,
        )
    )
    last = await session.scalar(
        select(TrainingPlan)
        .where(
            TrainingPlan.club_id == club.id,
            TrainingPlan.ownership_id == ownership.id,
            TrainingPlan.status == "applied",
        )
        .order_by(TrainingPlan.week_start.desc())
        .limit(1)
    )
    players = (
        await session.execute(
            select(Player.person_id, Person.name, Player.position)
            .join(Person, Person.id == Player.person_id)
            .where(Player.club_id == club.id, Player.status.in_(("active", "last_season")))
            .order_by(Person.name, Player.person_id)
        )
    ).all()
    if record:
        await user_effect(
            session, actor, "training_opened", Surface.CLUB, ownership=ownership, club_id=club.id
        )
    return TrainingScreen(
        club.name,
        ownership.id,
        week,
        week + timedelta(days=1),
        plan,
        last,
        tuple(TrainingMember(*r) for r in players),
        await training_blocked(session, club.id, week),
    )


async def select_training(
    session: AsyncSession,
    actor: Actor,
    *,
    ownership_id: UUID,
    week: datetime,
    focus: Focus,
    intensity: Intensity,
    executor_id: UUID | None = None,
    clock: Callable[[], datetime] | None = None,
) -> TrainingPlan:
    if not isinstance(focus, Focus) or not isinstance(intensity, Intensity):
        raise ValueError("Invalid training choice")
    ownership, club = await owned_club(session, actor)
    at = max(actor.at, clock() if clock else datetime.now(UTC))
    require_utc(at)
    if ownership.id != ownership_id or ownership.expires_at <= at:
        raise PermissionDenied("Training requires the current owner")
    current = week_start(at)
    if await training_blocked(session, club.id, current):
        raise ValueError("Training is unavailable on a match day")
    if week != current:
        raise ValueError("Training button belongs to a different UTC date")
    payload = {
        "focus": focus.value,
        "intensity": intensity.value,
        "executor_id": str(executor_id) if executor_id else None,
        "week": week.isoformat(),
    }
    previous = await session.get(TrainingDecision, actor.operation_id)
    if previous is not None:
        if previous.ownership_id != ownership.id or previous.payload != payload:
            raise PermissionDenied("Training operation reused with another choice")
        plan = await session.get(TrainingPlan, previous.plan_id)
        if plan is None:
            raise ValueError("Training receipt has no plan")
        return plan
    if focus == Focus.SET_PIECES:
        player = await session.scalar(
            select(Player)
            .where(
                Player.person_id == executor_id,
                Player.club_id == club.id,
                Player.status.in_(("active", "last_season")),
            )
            .with_for_update()
        )
        if player is None or player.injured_until is not None and player.injured_until > at:
            raise ValueError("Select an available set-piece taker")
    elif executor_id is not None:
        raise ValueError("Executor only applies to set pieces")
    plan = await session.scalar(
        select(TrainingPlan)
        .where(
            TrainingPlan.club_id == club.id,
            TrainingPlan.week_start == week,
            TrainingPlan.policy_version == "training-daily-v1",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if plan is not None and plan.status != "scheduled":
        raise ValueError("Training has already been processed for this UTC date")
    if plan is None:
        plan = TrainingPlan(
            id=uuid5(club.id, f"training:v1:{week.isoformat()}"),
            club_id=club.id,
            ownership_id=ownership.id,
            week_start=week,
            due_at=week + timedelta(days=1),
            selected_at=at,
            focus=focus.value,
            intensity=intensity.value,
            executor_id=executor_id,
            status="scheduled",
            policy_version="training-daily-v1",
            results=[],
        )
        session.add(plan)
    else:
        plan.ownership_id, plan.selected_at = ownership.id, at
        plan.focus, plan.intensity, plan.executor_id = focus.value, intensity.value, executor_id
    await session.flush()
    session.add(
        TrainingDecision(
            operation_id=actor.operation_id,
            plan_id=plan.id,
            ownership_id=ownership.id,
            payload=payload,
        )
    )
    await session.execute(
        insert(Job)
        .values(
            id=uuid5(plan.id, "job"),
            dedupe_key=f"training:v1:{plan.id}",
            kind="training.apply",
            payload={"version": 1, "plan_id": str(plan.id)},
            due_at=plan.due_at,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    await user_effect(
        session,
        replace(actor, at=at),
        "training_plan_saved",
        Surface.CLUB,
        ownership=ownership,
        club_id=club.id,
    )
    return plan


async def apply_training(session: AsyncSession, job: Job, at: datetime) -> None:
    require_utc(at)
    if (
        job.kind != "training.apply"
        or set(job.payload) != {"version", "plan_id"}
        or type(job.payload["version"]) is not int
        or job.payload["version"] != 1
    ):
        raise ValueError("Invalid training job")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Busy game transaction")
    plan = await session.scalar(
        select(TrainingPlan)
        .where(TrainingPlan.id == UUID(job.payload["plan_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if plan is None:
        raise ValueError("Missing training plan")
    if plan.status != "scheduled":
        return
    if (
        plan.policy_version != "training-daily-v1"
        or plan.due_at != plan.week_start + timedelta(days=1)
        or job.due_at != plan.due_at
    ):
        raise ValueError("Invalid training schedule or policy")
    if await training_blocked(session, plan.club_id, plan.week_start):
        plan.status = "cancelled"
        plan.results = [{"reason": "match_day"}]
        return
    if at < plan.due_at:
        raise NotReady("Training date has not ended")
    ownership = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.id == plan.ownership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    club = await session.scalar(select(Club).where(Club.id == plan.club_id).with_for_update())
    manager = await session.get(Manager, ownership.manager_id) if ownership else None
    if (
        ownership is None
        or club is None
        or manager is None
        or ownership.ended_at is not None
        or ownership.expires_at <= at
        or ownership.club_id != club.id
        or club.owner_telegram_id != manager.telegram_id
    ):
        plan.status = "cancelled"
        return
    players = (
        await session.scalars(
            select(Player)
            .where(Player.club_id == club.id, Player.status.in_(("active", "last_season")))
            .order_by(Player.person_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    ids = [p.person_id for p in players]
    people = {
        p.id: p
        for p in (
            await session.scalars(
                select(Person).where(Person.id.in_(ids)).order_by(Person.id).with_for_update()
            )
        ).all()
    }
    development = {
        d.person_id: d
        for d in (
            await session.scalars(
                select(PlayerDevelopment)
                .where(PlayerDevelopment.person_id.in_(ids))
                .with_for_update()
            )
        ).all()
    }
    positions = {
        p.person_id: p.position
        for p in (
            await session.scalars(
                select(LineupSlot).where(LineupSlot.club_id == club.id, LineupSlot.slot <= 11)
            )
        ).all()
    }
    staff = (
        await session.execute(
            select(Staff.role, StaffQualification.skill)
            .join(
                StaffQualification,
                (StaffQualification.person_id == Staff.person_id)
                & (StaffQualification.role == Staff.role),
            )
            .where(Staff.club_id == club.id, Staff.status.in_(("active", "last_season")))
        )
    ).all()
    qualifications: dict[str, int] = {}
    for role, skill in staff:
        qualifications[role] = max(qualifications.get(role, 0), skill)
    focus, intensity = Focus(plan.focus), Intensity(plan.intensity)
    results = []
    for player in players:
        person = people[player.person_id]
        if person.career != "player":
            raise ValueError("Invalid player career")
        values = {key: getattr(player, key) for key in SKILLS}
        dev = development.get(person.id)
        if dev is None:
            dev = PlayerDevelopment(
                person_id=person.id,
                caps=initial_caps(person.id, values),
                progress=dict.fromkeys(PROGRESS_KEYS, 0),
                policy_version=POLICY_VERSION,
            )
            session.add(dev)
        if dev.policy_version != POLICY_VERSION:
            raise ValueError("Unsupported development policy")
        trained = train_player(
            TrainingPlayer(
                person.id,
                person.age,
                positions.get(person.id, player.position),
                values,
                dev.caps,
                dev.progress,
                player.form,
                player.experience,
                player.injured_until is not None and player.injured_until > at,
            ),
            focus,
            intensity,
            seed=str(plan.id),
            bonus_bps=coach_bonus(focus, qualifications),
            physio_skill=qualifications.get("physiotherapist", 0),
            executor_id=plan.executor_id,
        )
        if not trained.trained:
            continue
        form_before, experience_before = player.form, player.experience
        progress_before = dict(dev.progress)
        changes = {
            key: {"before": values[key], "after": trained.skills[key]}
            for key in SKILLS
            if trained.skills[key] != values[key]
        }
        for key, value in trained.skills.items():
            setattr(player, key, value)
        dev.progress = trained.progress
        player.form, player.experience = trained.form, trained.experience
        if trained.injured:
            player.injured_until = at + timedelta(days=7)
        results.append(
            {
                "person_id": str(person.id),
                "name": person.name,
                "changes": changes,
                "form_before": form_before,
                "form_after": trained.form,
                "experience_before": experience_before,
                "experience_after": trained.experience,
                "gain_bps": trained.gain_bps,
                "progressed": trained.progress != progress_before
                or bool(changes)
                or trained.experience != experience_before,
                "injured": trained.injured,
            }
        )
    plan.results, plan.applied_at, plan.status = results, at, "applied"
