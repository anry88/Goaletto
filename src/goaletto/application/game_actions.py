"""Explicit manager actions and durable leases, independent of Telegram delivery."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.acquisition import telegram_operation
from goaletto.application.analytics import EVENT_SPECS, JourneyEvent, Origin, Surface, require_utc
from goaletto.application.match_runtime import NotReady
from goaletto.domain.ownership import calendar_months_after, ownership_deadline
from goaletto.infrastructure.analytics import record_event
from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import (
    AcceptedCommand,
    AnalyticsManager,
    Club,
    GameActionReceipt,
    Job,
    Manager,
    OutboxMessage,
    OwnershipPeriod,
)


@dataclass(frozen=True)
class Actor:
    manager_id: UUID
    telegram_id: int
    analytics_id: UUID | None
    operation_id: UUID
    at: datetime

    def __post_init__(self) -> None:
        require_utc(self.at)
        if (
            not isinstance(self.manager_id, UUID)
            or not isinstance(self.operation_id, UUID)
            or self.analytics_id is not None
            and not isinstance(self.analytics_id, UUID)
            or type(self.telegram_id) is not int
            or not 0 < self.telegram_id < 2**63
        ):
            raise ValueError("Invalid game actor")


@dataclass(frozen=True)
class LeasePolicy:
    reminder_months: tuple[int, ...] = (1, 3, 5)

    def __post_init__(self) -> None:
        if (
            any(type(v) is not int or not 1 <= v < 6 for v in self.reminder_months)
            or tuple(sorted(set(self.reminder_months))) != self.reminder_months
        ):
            raise ValueError("Reminder months must be distinct ordered intervals below six")


class PermissionDenied(ValueError):
    pass


DEFAULT_LEASE_POLICY = LeasePolicy()
GAME_ACTIONS = frozenset(
    {
        "club_claimed",
        "club_opened",
        "club_preview_opened",
        "lineup_opened",
        "lineup_saved",
        "next_fixture_opened",
        "match_opened",
        "match_command_accepted",
        "match_report_opened",
    }
)


async def allocation_lock(session: AsyncSession, *, try_lock: bool = False) -> bool:
    if not session.in_transaction():
        raise ValueError("Club operations need a transaction")
    if try_lock:
        return bool(
            await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
            )
        )
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
    )
    return True


async def actor_for(
    session: AsyncSession,
    telegram_id: int,
    update_id: int,
    at: datetime,
) -> Actor:
    require_utc(at)
    operation = telegram_operation(update_id)
    if type(telegram_id) is not int or not 0 < telegram_id < 2**63:
        raise PermissionDenied("Invalid Telegram identity")
    manager = await session.scalar(select(Manager).where(Manager.telegram_id == telegram_id))
    if manager is None:
        raise PermissionDenied("Start is required")
    analytics_id = await session.scalar(
        select(AnalyticsManager.id).where(AnalyticsManager.manager_id == manager.id)
    )
    return Actor(manager.id, manager.telegram_id, analytics_id, operation, at)


async def schedule_lease(
    session: AsyncSession,
    ownership: OwnershipPeriod,
    policy: LeasePolicy = DEFAULT_LEASE_POLICY,
) -> None:
    manager = await session.get(Manager, ownership.manager_id)
    if manager is None:
        raise ValueError("Lease has no manager")
    months = policy.reminder_months if manager.reminders_enabled and manager.bot_available else ()
    stages = tuple((f"remind:{month}", "ownership.remind", month) for month in months) + (
        ("expire", "ownership.expire", 6),
    )
    for stage, kind, month in stages:
        payload = {
            "version": 1,
            "ownership_id": str(ownership.id),
            "last_action_at": ownership.last_game_action_at.isoformat(),
            "month": month,
        }
        await session.execute(
            insert(Job)
            .values(
                id=uuid5(ownership.id, f"lease:v1:{stage}"),
                dedupe_key=f"lease:v1:{ownership.id}:{stage}",
                kind=kind,
                payload=payload,
                due_at=calendar_months_after(ownership.last_game_action_at, month),
                status="pending",
                attempts=0,
            )
            .on_conflict_do_update(
                index_elements=["dedupe_key"],
                set_={
                    "payload": payload,
                    "due_at": calendar_months_after(ownership.last_game_action_at, month),
                    "status": "pending",
                    "last_error": None,
                },
            )
        )


async def end_expired(
    session: AsyncSession,
    ownership: OwnershipPeriod,
    at: datetime,
) -> bool:
    require_utc(at)
    await allocation_lock(session)
    current = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.id == ownership.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None:
        return False
    ownership = current
    if ownership.ended_at is not None or at < ownership.expires_at:
        return False
    manager = await session.get(Manager, ownership.manager_id)
    club = await session.scalar(select(Club).where(Club.id == ownership.club_id).with_for_update())
    if club is None or manager is None or club.owner_telegram_id != manager.telegram_id:
        raise ValueError("Ownership projection is inconsistent")
    ownership.ended_at = ownership.expires_at
    ownership.end_reason = "inactive"
    club.owner_telegram_id = None
    await session.execute(
        update(AcceptedCommand)
        .where(AcceptedCommand.ownership_id == ownership.id, AcceptedCommand.status == "accepted")
        .values(status="cancelled")
    )
    operation = uuid5(ownership.id, "inactive-release:v1")
    analytics_id = await session.scalar(
        select(AnalyticsManager.id).where(AnalyticsManager.manager_id == manager.id)
    )
    await record_event(
        session,
        JourneyEvent(
            event_id=uuid5(operation, "event"),
            name="club_ownership_ended",
            origin=Origin.SYSTEM,
            operation_id=operation,
            effect_number=0,
            surface=Surface.LIFECYCLE,
            occurred_at=ownership.ended_at,
            recorded_at=at,
            analytics_manager_id=analytics_id,
            ownership_id=ownership.id,
            club_id=club.id,
        ),
    )
    return True


async def owned_club(session: AsyncSession, actor: Actor) -> tuple[OwnershipPeriod, Club]:
    await allocation_lock(session)
    ownership = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.manager_id == actor.manager_id, OwnershipPeriod.ended_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ownership is None or ownership.expires_at <= actor.at:
        raise PermissionDenied("No current ownership")
    club = await session.scalar(select(Club).where(Club.id == ownership.club_id).with_for_update())
    if club is None or club.owner_telegram_id != actor.telegram_id:
        raise PermissionDenied("Current club ownership does not match the actor")
    return ownership, club


async def user_effect(
    session: AsyncSession,
    actor: Actor,
    name: str,
    surface: Surface,
    *,
    effect: int = 0,
    ownership: OwnershipPeriod | None = None,
    club_id: UUID | None = None,
    fixture_id: UUID | None = None,
    command_id: UUID | None = None,
    policy: LeasePolicy = DEFAULT_LEASE_POLICY,
) -> bool:
    spec = EVENT_SPECS.get(name)
    if type(effect) is not int or not 0 <= effect <= 32767 or not isinstance(surface, Surface):
        raise ValueError("Invalid game effect envelope")
    if (
        spec is None
        or name not in GAME_ACTIONS
        or spec.origin != Origin.USER
        or name in {"manager_started", "campaign_touched", "start_accepted"}
    ):
        raise ValueError("Unsupported game action")
    refs = {
        "club_id": club_id,
        "ownership_id": ownership.id if ownership is not None else None,
        "fixture_id": fixture_id,
        "command_id": command_id,
    }
    if any(not isinstance(refs.get(key), UUID) for key in spec.required_refs):
        raise ValueError("Game action needs its durable references, even without analytics")
    if spec.ownership_activity:
        await allocation_lock(session)
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == actor.manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None or manager.telegram_id != actor.telegram_id:
        raise PermissionDenied("Manager identity does not match the actor")
    if ownership is not None:
        ownership = await session.scalar(
            select(OwnershipPeriod)
            .where(OwnershipPeriod.id == ownership.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    if spec.ownership_activity and (
        ownership is None
        or ownership.manager_id != actor.manager_id
        or ownership.club_id != club_id
        or ownership.ended_at is not None
        or ownership.expires_at <= actor.at
    ):
        raise PermissionDenied("Game action requires the current ownership")
    previous = await session.get(GameActionReceipt, (actor.operation_id, effect))
    if previous is not None:
        if (
            previous.manager_id != actor.manager_id
            or previous.kind != name
            or previous.club_id != club_id
            or previous.ownership_id != (ownership.id if ownership is not None else None)
        ):
            raise PermissionDenied("Action identity was reused with a different scope")
        return False
    session.add(
        GameActionReceipt(
            operation_id=actor.operation_id,
            effect_number=effect,
            manager_id=actor.manager_id,
            ownership_id=ownership.id if ownership is not None else None,
            club_id=club_id,
            kind=name,
            occurred_at=actor.at,
        )
    )
    await session.flush()
    if actor.analytics_id is not None:
        await record_event(
            session,
            JourneyEvent(
                event_id=uuid5(actor.operation_id, f"event:{effect}"),
                name=name,
                origin=Origin.USER,
                operation_id=actor.operation_id,
                effect_number=effect,
                surface=surface,
                occurred_at=actor.at,
                recorded_at=actor.at,
                analytics_manager_id=actor.analytics_id,
                session_id=uuid5(actor.operation_id, "session-proposal"),
                ownership_id=ownership.id if ownership is not None else None,
                club_id=club_id,
                fixture_id=fixture_id,
                command_id=command_id,
            ),
        )
    if spec.ownership_activity and ownership is not None:
        ownership.last_game_action_at = max(ownership.last_game_action_at, actor.at)
        ownership.expires_at = ownership_deadline(ownership.last_game_action_at)
        await schedule_lease(session, ownership, policy)
    return True


async def handle_lease_job(session: AsyncSession, job: Job, at: datetime) -> None:
    require_utc(at)
    if (
        job.kind not in {"ownership.remind", "ownership.expire"}
        or set(job.payload) != {"version", "ownership_id", "last_action_at", "month"}
        or type(job.payload["version"]) is not int
        or job.payload["version"] != 1
    ):
        raise ValueError("Unsupported lease job")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Allocation is busy")
    ownership_id = UUID(job.payload["ownership_id"])
    expected = datetime.fromisoformat(job.payload["last_action_at"])
    require_utc(expected)
    month = job.payload["month"]
    if (
        type(month) is not int
        or not 1 <= month <= 6
        or (job.kind == "ownership.expire") != (month == 6)
    ):
        raise ValueError("Invalid lease interval")
    ownership = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.id == ownership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        ownership is None
        or ownership.ended_at is not None
        or ownership.last_game_action_at != expected
    ):
        return
    if at < calendar_months_after(expected, month):
        raise NotReady("Lease deadline has not arrived")
    if job.kind == "ownership.expire":
        await end_expired(session, ownership, at)
        return
    manager = await session.get(Manager, ownership.manager_id)
    if (
        manager is None
        or not manager.reminders_enabled
        or not manager.bot_available
        or ownership.expires_at <= at
    ):
        return
    await session.execute(
        insert(OutboxMessage)
        .values(
            id=uuid5(job.id, f"reminder:{expected.isoformat()}"),
            dedupe_key=f"lease:v1:{ownership.id}:{expected.isoformat()}:remind:{month}",
            chat_id=manager.telegram_id,
            due_at=at,
            status="pending",
            attempts=0,
            payload={
                "version": 1,
                "kind": "ownership_reminder",
                "ownership_id": str(ownership.id),
                "club_id": str(ownership.club_id),
                "last_action_at": expected.isoformat(),
            },
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
