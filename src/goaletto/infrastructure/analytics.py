"""Transactional collection. Never commits, exports, or calls external services."""

import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4, uuid5

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import (
    CONTRACT_VERSION,
    EVENT_SPECS,
    JourneyEvent,
    Origin,
    continues_session,
    require_utc,
)
from goaletto.infrastructure.models import (
    AcquisitionFirstTouch,
    AcquisitionTouch,
    AnalyticsCoverage,
    AnalyticsExclusion,
    AnalyticsManager,
    JourneyEventRow,
    JourneySession,
    Manager,
)


class CoverageNotStarted(ValueError):
    pass


def event_envelope(event: JourneyEvent) -> dict[str, object]:
    # Dataclass validation and a finite set of fields are the only payload source.
    result: dict[str, object] = json.loads(
        json.dumps(
            asdict(event), default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)
        )
    )
    return result


def same_effect(existing: dict[str, object], incoming: dict[str, object]) -> bool:
    ignored = {"event_id", "recorded_at", "session_id"}
    return {k: v for k, v in existing.items() if k not in ignored} == {
        k: v for k, v in incoming.items() if k not in ignored
    }


async def start_coverage(session: AsyncSession, at: datetime, retention_days: int = 180) -> None:
    """Explicit rollout operation; preview handlers never call this."""
    require_utc(at)
    if type(retention_days) is not int or not 1 <= retention_days <= 730:
        raise ValueError("Invalid retention policy")
    await session.execute(
        insert(AnalyticsCoverage)
        .values(version=CONTRACT_VERSION, started_at=at, retention_days=retention_days)
        .on_conflict_do_nothing(index_elements=["version"])
    )


async def pseudonym_for(session: AsyncSession, manager_id: UUID, at: datetime) -> UUID | None:
    """Create mapping before claim, serialized with exclusion/deletion for this manager."""
    require_utc(at)
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None:
        raise ValueError("Unknown manager")
    exclusion = await session.scalar(
        select(AnalyticsExclusion).where(AnalyticsExclusion.manager_id == manager_id)
    )
    if exclusion is not None or manager.is_internal:
        return None
    await session.execute(
        insert(AnalyticsManager)
        .values(id=uuid4(), manager_id=manager_id, created_at=at)
        .on_conflict_do_nothing(index_elements=["manager_id"])
    )
    result = await session.scalar(
        select(AnalyticsManager.id).where(AnalyticsManager.manager_id == manager_id)
    )
    if result is None:
        raise ValueError("Mapping was not persisted")
    return result


async def record_event(session: AsyncSession, event: JourneyEvent) -> bool:
    """Share the caller's game transaction; true means a new logical effect.

    The supplied user session ID is a proposal, replaced by the durable session.
    A rejected action attaches to an existing session but never creates/extends it.
    Event time/operation/effect must remain stable on retry; recorded_at may change.
    """
    if not session.in_transaction():
        raise ValueError("Collector requires the caller's game transaction")
    coverage = await session.scalar(
        select(AnalyticsCoverage).where(AnalyticsCoverage.version == event.version)
    )
    if coverage is None:
        raise CoverageNotStarted("Collection needs explicit coverage activation")
    if event.occurred_at < coverage.started_at:
        if (
            event.origin == Origin.SYSTEM
            and event.analytics_manager_id is None
            and getattr(session, "info", {}).get("world_replay_recorded_at") is not None
        ):
            # Newly calculated bot history does not retroactively establish
            # analytics coverage before collection actually started.
            return False
        raise ValueError("Event precedes declared collection coverage")
    if event.analytics_manager_id is not None:
        mapping = await session.scalar(
            select(AnalyticsManager).where(AnalyticsManager.id == event.analytics_manager_id)
        )
        if mapping is None:
            return False  # Deleted/opted-out identities are never reconstructed from an event.
        manager = await session.scalar(
            select(Manager)
            .where(Manager.id == mapping.manager_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        excluded = await session.scalar(
            select(AnalyticsExclusion).where(AnalyticsExclusion.manager_id == mapping.manager_id)
        )
        if manager is None or manager.is_internal or excluded is not None:
            return False
    else:
        manager = None
    existing = await session.scalar(
        select(JourneyEventRow).where(
            JourneyEventRow.version == event.version,
            JourneyEventRow.operation_id == event.operation_id,
            JourneyEventRow.effect_number == event.effect_number,
        )
    )
    if existing is not None:
        if not same_effect(existing.envelope, event_envelope(event)):
            raise ValueError("Logical effect key was reused with different content")
        return False
    if event.name == "manager_started":
        first = await session.scalar(
            select(JourneyEventRow).where(
                JourneyEventRow.analytics_manager_id == event.analytics_manager_id,
                JourneyEventRow.name == "manager_started",
            )
        )
        if first is not None or (manager is not None and manager.first_game_start_at is not None):
            return False  # Retention cleanup must not turn a returning manager into a new cohort.
    spec = EVENT_SPECS[event.name]
    if event.origin == Origin.USER:
        previous = await session.scalar(
            select(JourneySession)
            .where(JourneySession.analytics_manager_id == event.analytics_manager_id)
            .order_by(JourneySession.last_action_at.desc())
            .limit(1)
        )
        if spec.user_activity:
            if previous is not None and (
                previous.first_action_at <= event.occurred_at <= previous.last_action_at
                or continues_session(previous.last_action_at, event.occurred_at)
            ):
                previous.last_action_at = max(previous.last_action_at, event.occurred_at)
                session_id = previous.id
            else:
                session_id = uuid5(event.operation_id, f"session:{event.effect_number}")
                session.add(
                    JourneySession(
                        id=session_id,
                        analytics_manager_id=event.analytics_manager_id,
                        first_action_at=event.occurred_at,
                        last_action_at=event.occurred_at,
                    )
                )
                await session.flush()
            event = replace(event, session_id=session_id)
        else:
            event = replace(event, session_id=previous.id if previous is not None else None)
    statement = (
        insert(JourneyEventRow)
        .values(
            id=event.event_id,
            version=event.version,
            operation_id=event.operation_id,
            effect_number=event.effect_number,
            name=event.name,
            origin=event.origin.value,
            analytics_manager_id=event.analytics_manager_id,
            session_id=event.session_id,
            occurred_at=event.occurred_at,
            recorded_at=event.recorded_at,
            user_activity=spec.user_activity,
            envelope=event_envelope(event),
        )
        .on_conflict_do_nothing(index_elements=["version", "operation_id", "effect_number"])
        .returning(JourneyEventRow.id)
    )
    inserted = await session.scalar(statement)
    if inserted is None:
        existing = await session.scalar(
            select(JourneyEventRow).where(
                JourneyEventRow.version == event.version,
                JourneyEventRow.operation_id == event.operation_id,
                JourneyEventRow.effect_number == event.effect_number,
            )
        )
        if existing is None or not same_effect(existing.envelope, event_envelope(event)):
            raise ValueError("Conflicting logical effect")
        return False
    if event.name == "manager_started" and manager is not None:
        manager.first_game_start_at = event.occurred_at
    return True


async def exclude_manager(
    session: AsyncSession,
    manager_id: UUID,
    kind: str,
    at: datetime,
) -> None:
    require_utc(at)
    if kind not in {"internal", "test", "operator", "opt_out"}:
        raise ValueError("Unknown exclusion kind")
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None:
        raise ValueError("Unknown manager")
    await session.execute(
        insert(AnalyticsExclusion)
        .values(manager_id=manager_id, kind=kind, excluded_at=at)
        .on_conflict_do_update(
            index_elements=["manager_id"], set_={"kind": kind, "excluded_at": at}
        )
    )
    if kind != "opt_out":
        manager.is_internal = True
    # Cascades erase events/sessions and the restricted mapping, while the tombstone remains.
    await session.execute(delete(AnalyticsManager).where(AnalyticsManager.manager_id == manager_id))


async def cleanup_events(session: AsyncSession, at: datetime) -> None:
    require_utc(at)
    coverage = await session.scalar(
        select(AnalyticsCoverage).where(AnalyticsCoverage.version == CONTRACT_VERSION)
    )
    if coverage is None:
        raise CoverageNotStarted("Collection needs explicit coverage activation")
    cutoff = at - timedelta(days=coverage.retention_days)
    await session.execute(delete(JourneyEventRow).where(JourneyEventRow.recorded_at < cutoff))
    await session.execute(delete(AcquisitionTouch).where(AcquisitionTouch.recorded_at < cutoff))
    await session.execute(
        delete(AcquisitionFirstTouch).where(AcquisitionFirstTouch.started_at < cutoff)
    )
    await session.execute(
        delete(JourneySession).where(
            JourneySession.last_action_at < cutoff,
            ~select(JourneyEventRow.id)
            .where(JourneyEventRow.session_id == JourneySession.id)
            .exists(),
        )
    )
