"""Durable daily choices and atomic development; no Telegram/network calls."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.development import growth_budget, save_budget
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.application.match_runtime import NotReady
from goaletto.application.recovery import recover_player
from goaletto.application.training_staff import qualifications, staff_roster, training_staff
from goaletto.domain.game_cycle import HORIZON_DAYS, midnight
from goaletto.domain.kickoff import BASELINE_MINUTE, KickoffPreference, preference_utc
from goaletto.domain.training import (
    POLICY_VERSION,
    PROGRESS_KEYS,
    SESSION_POLICY,
    SKILLS,
    SPECIALISTS,
    Focus,
    Intensity,
    TrainingPlayer,
    available_focus,
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
    Season,
    SeasonClub,
    TrainingDecision,
    TrainingPlan,
)


@dataclass(frozen=True)
class TrainingMember:
    id: UUID
    name: str
    position: str


@dataclass(frozen=True)
class TrainingDate:
    day: datetime
    reason: str | None = None
    opponent: str | None = None
    competition: str | None = None
    selected: bool = False


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
    available: tuple[Focus, ...] = (Focus.TEAM,)
    staff_changed: bool = False
    dates: tuple[TrainingDate, ...] = ()
    block_reason: TrainingDate | None = None
    zone: str = "UTC"


def week_start(at: datetime) -> datetime:
    require_utc(at)
    return midnight(at.date())


async def training_dates(
    session: AsyncSession, club_id: UUID, at: datetime, days: tuple[datetime, ...]
) -> tuple[TrainingDate, ...]:
    first, until = min(days), max(days) + timedelta(days=1)
    fixtures = (
        await session.scalars(
            select(Fixture).where(
                (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
                Fixture.status != "cancelled",
                Fixture.scheduled_start_at < until,
                (Fixture.scheduled_start_at >= first) | Fixture.status.in_(("live", "paused")),
            )
        )
    ).all()
    plans = (
        await session.scalars(
            select(FixturePlan).where(
                (FixturePlan.home_club_id == club_id)
                | (FixturePlan.away_club_id == club_id)
                | FixturePlan.possible_club_ids.contains([str(club_id)]),
                (
                    (FixturePlan.assigned_day >= first.date())
                    & (FixturePlan.assigned_day < until.date())
                )
                | FixturePlan.id.in_([f.id for f in fixtures]),
            )
        )
    ).all()
    combined: tuple[Fixture | FixturePlan, ...] = (*fixtures, *plans)
    club_ids = {c for f in combined for c in (f.home_club_id, f.away_club_id) if c and c != club_id}
    names = {
        cid: name
        for cid, name in (
            await session.execute(select(Club.id, Club.name).where(Club.id.in_(club_ids)))
        ).all()
    }
    known_end = await session.scalar(
        select(func.max(Season.ends_at))
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(SeasonClub.club_id == club_id)
    )
    chosen = set(
        (
            await session.scalars(
                select(TrainingPlan.week_start).where(
                    TrainingPlan.club_id == club_id,
                    TrainingPlan.week_start >= first,
                    TrainingPlan.week_start < until,
                    TrainingPlan.status.in_(("scheduled", "applied")),
                )
            )
        ).all()
    )
    result = []
    for day in days:
        fixture = next(
            (
                f
                for f in fixtures
                if f.scheduled_start_at.date() == day.date()
                or (
                    f.status in {"live", "paused"}
                    and f.scheduled_start_at < day
                    and day.date() <= at.date()
                )
            ),
            None,
        )
        plan = next((p for p in plans if p.assigned_day == day.date()), None)
        opponent_id = None
        reason = competition = None
        if fixture:
            actual_plan = next((p for p in plans if p.id == fixture.id), None)
            reason, competition = "match", actual_plan.competition if actual_plan else "league"
            opponent_id = (
                fixture.away_club_id if fixture.home_club_id == club_id else fixture.home_club_id
            )
        elif plan:
            conditional = club_id not in (plan.home_club_id, plan.away_club_id)
            reason, competition = ("conditional" if conditional else "match"), plan.competition
            opponent_id = plan.away_club_id if plan.home_club_id == club_id else plan.home_club_id
        elif known_end is not None and day >= known_end:
            reason = "calendar_pending"
        result.append(TrainingDate(day, reason, names.get(opponent_id), competition, day in chosen))
    return tuple(result)


async def training_blocked(
    session: AsyncSession, club_id: UUID, day: datetime, *, at: datetime | None = None
) -> bool:
    return (await training_dates(session, club_id, at or day, (day,)))[0].reason is not None


def preferred_training_time(day: datetime, manager: Manager | None) -> datetime:
    preference = (
        KickoffPreference(manager.timezone_name, manager.preferred_kickoff_minute)
        if manager is not None and manager.preferred_kickoff_minute is not None
        else KickoffPreference("UTC", BASELINE_MINUTE)
    )
    return preference_utc(day.date(), preference)


async def load_training(
    session: AsyncSession, actor: Actor, *, record: bool = True, day: datetime | None = None
) -> TrainingScreen:
    ownership, club = await owned_club(session, actor)
    current = week_start(actor.at)
    week = day if day is not None else current
    require_utc(week)
    if week != week_start(week) or not current <= week < current + timedelta(days=HORIZON_DAYS):
        raise ValueError("Training date is outside the planning horizon")
    dates = await training_dates(
        session, club.id, actor.at, tuple(current + timedelta(days=i) for i in range(HORIZON_DAYS))
    )
    plan = await session.scalar(
        select(TrainingPlan)
        .where(
            TrainingPlan.club_id == club.id,
            TrainingPlan.week_start == week,
            TrainingPlan.ownership_id == ownership.id,
        )
        .order_by(
            (TrainingPlan.status != "cancelled").desc(),
            TrainingPlan.selected_at.desc(),
            TrainingPlan.id,
        )
        .limit(1)
    )
    manager = await session.get(Manager, actor.manager_id)
    due = (
        plan.due_at
        if plan and plan.status in {"scheduled", "applied"}
        else preferred_training_time(week, manager)
    )
    dates = tuple(
        replace(d, reason="time_passed")
        if not d.reason and not d.selected and preferred_training_time(d.day, manager) <= actor.at
        else d
        for d in dates
    )
    selected_date = next(d for d in dates if d.day == week)
    last = await session.scalar(
        select(TrainingPlan)
        .where(
            TrainingPlan.club_id == club.id,
            TrainingPlan.ownership_id == ownership.id,
            TrainingPlan.status.in_(("applied", "cancelled")),
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
    roster = await staff_roster(session, club.id, actor.at)
    allowed = tuple(f for f in Focus if available_focus(f, qualifications(roster)))
    changed = bool(
        plan
        and plan.status == "scheduled"
        and plan.staff_snapshot != training_staff(roster, Focus(plan.focus))
    )
    if record:
        await user_effect(
            session, actor, "training_opened", Surface.CLUB, ownership=ownership, club_id=club.id
        )
    return TrainingScreen(
        club.name,
        ownership.id,
        week,
        due,
        plan,
        last,
        tuple(TrainingMember(*r) for r in players),
        selected_date.reason is not None,
        allowed,
        changed,
        dates,
        selected_date,
        manager.timezone_name if manager else "UTC",
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
    if week != week_start(week) or not current <= week < current + timedelta(days=HORIZON_DAYS):
        raise ValueError("Training date is outside the planning horizon")
    if await training_blocked(session, club.id, week, at=at):
        raise ValueError("Training is unavailable on a match day")
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
    manager = await session.get(Manager, actor.manager_id)
    due = preferred_training_time(week, manager)
    if due <= at:
        raise ValueError("Preferred training time has passed; select another UTC date")
    roster = await staff_roster(session, club.id, at)
    if not available_focus(focus, qualifications(roster)):
        raise ValueError("Training requires a specialist")
    snapshot = training_staff(roster, focus)
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
        if (
            player is None
            or player.injured_until is not None
            and player.injured_until > max(at, week)
        ):
            raise ValueError("Select an available set-piece taker")
    elif executor_id is not None:
        raise ValueError("Executor only applies to set pieces")
    plan = await session.scalar(
        select(TrainingPlan)
        .where(
            TrainingPlan.club_id == club.id,
            TrainingPlan.week_start == week,
            TrainingPlan.status != "cancelled",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if plan is not None and (plan.status != "scheduled" or at >= plan.due_at):
        raise ValueError("Training has already started or been processed for this UTC date")
    if plan is None:
        plan = TrainingPlan(
            id=uuid5(actor.operation_id, f"training:v2:{week.isoformat()}"),
            club_id=club.id,
            ownership_id=ownership.id,
            week_start=week,
            due_at=due,
            schedule_policy="training-preference-v1",
            selected_at=at,
            focus=focus.value,
            intensity=intensity.value,
            executor_id=executor_id,
            status="scheduled",
            policy_version=SESSION_POLICY,
            staff_snapshot=snapshot,
            results=[],
        )
        session.add(plan)
    else:
        plan.ownership_id, plan.selected_at = ownership.id, at
        plan.due_at, plan.schedule_policy = due, "training-preference-v1"
        plan.focus, plan.intensity, plan.executor_id = focus.value, intensity.value, executor_id
        plan.staff_snapshot = snapshot
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
        .on_conflict_do_update(
            index_elements=["dedupe_key"],
            set_={"due_at": plan.due_at},
            where=Job.status == "pending",
        )
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
        plan.policy_version != SESSION_POLICY
        or not (
            plan.schedule_policy == "training-midnight-v1"
            and plan.due_at == plan.week_start + timedelta(days=1)
            or plan.schedule_policy == "training-preference-v1"
            and plan.week_start <= plan.due_at < plan.week_start + timedelta(days=1)
        )
        or job.due_at != plan.due_at
    ):
        raise ValueError("Invalid training schedule or policy")
    if at < plan.due_at:
        raise NotReady("Training time has not arrived")
    if await training_blocked(session, plan.club_id, plan.week_start):
        plan.status = "cancelled"
        plan.results = [{"reason": "match_day"}]
        return
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
        plan.results = [{"reason": "ownership_changed"}]
        return
    roster = await staff_roster(session, club.id, at)
    staff_skills = qualifications(roster)
    focus, intensity = Focus(plan.focus), Intensity(plan.intensity)
    if not available_focus(focus, staff_skills) or plan.staff_snapshot != training_staff(
        roster, focus
    ):
        plan.status = "cancelled"
        plan.results = [{"reason": "staff_changed"}]
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
    results = []
    for player in players:
        person = people[player.person_id]
        if person.career != "player":
            raise ValueError("Invalid player career")
        condition = await recover_player(session, person.id, at)
        if condition.active_match_id is not None:
            continue
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
                player.morale,
                player.leadership,
                dev.leadership_progress or 0,
            ),
            focus,
            intensity,
            seed=str(plan.id),
            trainer_skill=staff_skills.get(
                "head_coach" if focus == Focus.TEAM else SPECIALISTS[focus], 0
            ),
            head_skill=staff_skills.get("head_coach", 0),
            physio_skill=staff_skills.get("physiotherapist", 0),
            budget=growth_budget(dev, person.age),
            executor_id=plan.executor_id,
        )
        if not trained.trained:
            continue
        form_before, experience_before = player.form, player.experience
        morale_before, leadership_before = player.morale, player.leadership
        progress_before = dict(dev.progress)
        changes = {
            key: {"before": values[key], "after": trained.skills[key]}
            for key in SKILLS
            if trained.skills[key] != values[key]
        }
        for key, value in trained.skills.items():
            setattr(player, key, value)
        dev.progress = trained.progress
        save_budget(dev, trained.budget)
        dev.leadership_progress = trained.leadership_progress
        player.morale, player.leadership = trained.morale, trained.leadership
        player.form, player.experience = trained.form, trained.experience
        condition.energy_bps = max(
            0,
            condition.energy_bps
            - {
                Intensity.LOW: 80,
                Intensity.MEDIUM: 140,
                Intensity.HIGH: 220,
            }[intensity],
        )
        condition.stamina = player.stamina
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
                "morale_before": morale_before,
                "morale_after": trained.morale,
                "leadership_before": leadership_before,
                "leadership_after": trained.leadership,
                "leadership_gain": trained.leadership_gain,
                "season_gains": dict(trained.budget.used),
                "development_age": trained.budget.age,
                "progressed": trained.progress != progress_before
                or bool(changes)
                or trained.experience != experience_before,
                "injured": trained.injured,
            }
        )
    plan.results, plan.applied_at, plan.status = results, at, "applied"


async def reserve_match_day(
    session: AsyncSession, club_ids: tuple[UUID, ...], day: date, at: datetime, *, competition: str
) -> None:
    """Shared tournament/friendly gate: serialize against selections and receipts."""
    require_utc(at)
    if competition not in {"league", "cup", "champions", "friendly"}:
        raise ValueError("Unknown competition")
    await allocation_lock(session)
    plans = (
        await session.scalars(
            select(TrainingPlan)
            .where(TrainingPlan.club_id.in_(club_ids), TrainingPlan.week_start == midnight(day))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    if competition == "friendly":
        if any(p.status in {"scheduled", "applied"} for p in plans):
            raise ValueError("Training already occupies this date")
        return
    from goaletto.infrastructure.models import OutboxMessage

    for plan in plans:
        if plan.status != "scheduled":
            continue
        plan.status, plan.results = "cancelled", [{"reason": "match_day"}]
        owner = await session.get(OwnershipPeriod, plan.ownership_id)
        club = await session.get(Club, plan.club_id)
        manager = await session.get(Manager, owner.manager_id) if owner else None
        if (
            not owner
            or not club
            or not manager
            or owner.ended_at is not None
            or owner.club_id != club.id
            or owner.expires_at <= at
            or club.owner_telegram_id != manager.telegram_id
        ):
            continue
        key = f"training:cancelled:{plan.id}"
        await session.execute(
            insert(OutboxMessage)
            .values(
                id=uuid5(plan.id, key),
                dedupe_key=key,
                chat_id=manager.telegram_id,
                payload={
                    "version": 1,
                    "kind": "training_cancelled",
                    "plan_id": str(plan.id),
                    "ownership_id": str(owner.id),
                    "club_id": str(club.id),
                    "day": day.isoformat(),
                },
                due_at=at,
                priority=70,
                status="pending",
                attempts=0,
            )
            .on_conflict_do_nothing()
        )
    await session.flush()


async def cancel_training(
    session: AsyncSession, actor: Actor, plan_id: UUID, ownership_id: UUID
) -> None:
    owner, club = await owned_club(session, actor)
    plan = await session.get(TrainingPlan, plan_id, with_for_update=True)
    if (
        plan is None
        or owner.id != ownership_id
        or plan.ownership_id != owner.id
        or plan.club_id != club.id
    ):
        raise PermissionDenied("Training belongs to another ownership")
    if plan.status != "scheduled":
        return
    if actor.at >= plan.due_at:
        raise ValueError("Training time has already arrived")
    plan.status, plan.results = "cancelled", [{"reason": "manager_cancelled"}]
    await user_effect(
        session, actor, "training_plan_cancelled", Surface.CLUB, ownership=owner, club_id=club.id
    )
    await session.flush()
