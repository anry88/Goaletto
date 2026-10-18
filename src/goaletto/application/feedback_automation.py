"""Explicitly enabled internal feedback; mature facts, no inferred churn."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.feedback import CooldownActive, FeedbackSettings, open_prompt
from goaletto.application.game_actions import Actor, PermissionDenied, allocation_lock
from goaletto.application.match_runtime import NotReady
from goaletto.infrastructure.models import (
    AnalyticsExclusion,
    Club,
    FeedbackResponse,
    GameActionReceipt,
    Job,
    Manager,
    OutboxMessage,
    OwnershipPeriod,
)
from goaletto.infrastructure.onboarding_queries import ALL_OPPORTUNITY_DETAILS_SQL

SCAN_ID = uuid5(NAMESPACE_URL, "goaletto:feedback-scan:v1")
CAMPAIGNS = {"entry_help": "entry-help-v1", "missed_match": "missed-match-v1"}
BATCH = 50

# This uses the exact same calendar/ownership/maturity classification as the
# public KPI. Internal actions come from operational receipts, not fake journey
# identities; internal people never acquire analytics mappings for this scan.
MISSED_SQL = (
    """
WITH cohort AS (
 SELECT id AS analytics_manager_id, id AS manager_id, first_game_start_at AS started_at
 FROM managers WHERE id=:manager AND is_internal AND first_game_start_at >= :coverage
), journey_events AS (
 SELECT manager_id AS analytics_manager_id, 1 AS version, 'user' AS origin,
   true AS user_activity, kind AS name, occurred_at, occurred_at AS recorded_at,
   jsonb_build_object('fixture_id',fixture_id,'ownership_id',ownership_id) AS envelope
 FROM game_action_receipts WHERE manager_id=:manager
)
"""
    + ALL_OPPORTUNITY_DETAILS_SQL
    + """
SELECT p.id, p.ownership_id FROM classified p
WHERE p.state='mature' AND NOT p.participated AND NOT p.late
AND p.actual_finish_at + INTERVAL '24 hours' >= :report_at - INTERVAL '24 hours'
AND NOT EXISTS (SELECT 1 FROM classified earlier WHERE earlier.ordinal < p.ordinal
  AND earlier.state IN ('waiting','coverage','unavailable'))
AND NOT EXISTS (SELECT 1 FROM game_action_receipts unknown_scope
  WHERE unknown_scope.manager_id=:manager AND unknown_scope.ownership_id=p.ownership_id
  AND unknown_scope.kind IN ('match_opened','match_report_opened','match_command_accepted')
  AND unknown_scope.fixture_id IS NULL
  AND unknown_scope.occurred_at >= GREATEST(p.claimed_at,p.actual_start_at-INTERVAL '24 hours')
  AND unknown_scope.occurred_at < p.actual_finish_at+INTERVAL '24 hours')
ORDER BY p.ordinal LIMIT 1
"""
)


@dataclass(frozen=True)
class Candidate:
    kind: str
    ownership_id: UUID | None
    fixture_id: UUID | None


async def active_owner(
    session: AsyncSession, manager: Manager, at: datetime
) -> OwnershipPeriod | None:
    owner = await session.scalar(
        select(OwnershipPeriod)
        .join(Club, Club.id == OwnershipPeriod.club_id)
        .where(
            Club.owner_telegram_id == manager.telegram_id,
            OwnershipPeriod.manager_id == manager.id,
            OwnershipPeriod.ended_at.is_(None),
            OwnershipPeriod.expires_at > at,
        )
    )

    return owner


async def candidate(
    session: AsyncSession, manager: Manager, at: datetime, coverage: datetime
) -> Candidate | None:
    if not manager.is_internal or not manager.bot_available or manager.first_game_start_at is None:
        return None
    exclusion = await session.get(AnalyticsExclusion, manager.id)
    if exclusion is not None and exclusion.kind in {"opt_out", "operator"}:
        return None
    if manager.first_game_start_at < coverage:
        return None
    recent = await session.scalar(
        select(GameActionReceipt.operation_id)
        .where(
            GameActionReceipt.manager_id == manager.id,
            GameActionReceipt.occurred_at > at - timedelta(minutes=30),
        )
        .limit(1)
    )
    if recent is not None:
        return None
    owner = await active_owner(session, manager, at)
    if at - timedelta(hours=48) <= manager.first_game_start_at <= at - timedelta(hours=24):
        if owner is None:
            previous = await session.scalar(
                select(OwnershipPeriod.id).where(OwnershipPeriod.manager_id == manager.id).limit(1)
            )
            if previous is None:
                return Candidate("entry_help", None, None)
        elif not await session.scalar(
            select(GameActionReceipt.operation_id)
            .where(
                GameActionReceipt.manager_id == manager.id,
                GameActionReceipt.ownership_id == owner.id,
                GameActionReceipt.kind == "lineup_saved",
            )
            .limit(1)
        ):
            return Candidate("entry_help", owner.id, None)
    row = (
        await session.execute(
            text(MISSED_SQL), {"manager": manager.id, "coverage": coverage, "report_at": at}
        )
    ).first()
    if row is not None and owner is not None and owner.id == row.ownership_id:
        return Candidate("missed_match", row.ownership_id, row.id)
    return None


async def configure(
    session: AsyncSession, at: datetime, settings: FeedbackSettings, enabled: bool
) -> None:
    require_utc(at)
    existing = await session.get(Job, SCAN_ID, with_for_update=True)
    active = enabled and settings.enabled and settings.comment_retention_days is not None
    payload = {
        "version": 1,
        "coverage_at": at.isoformat(),
        "after": None,
        "enabled": active,
        "retention_days": settings.comment_retention_days,
        "cooldown_days": settings.cooldown_days,
    }
    if existing is not None:
        payload["coverage_at"] = existing.payload["coverage_at"]
        payload["after"] = existing.payload.get("after")
        existing.payload = payload
        if existing.status != "failed":
            existing.status, existing.due_at = ("pending" if active else "held"), at
        return
    if active:
        await session.execute(
            insert(Job)
            .values(
                id=SCAN_ID,
                dedupe_key="feedback-scan:v1",
                kind="feedback.scan",
                payload=payload,
                due_at=at,
                status="pending",
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )


async def scan(session: AsyncSession, job: Job, at: datetime) -> None:
    if job.payload.get("version") != 1 or not job.payload.get("enabled"):
        raise ValueError("Unsupported feedback scan")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Ownership is busy")
    coverage = datetime.fromisoformat(job.payload["coverage_at"])
    settings = FeedbackSettings(True, job.payload["retention_days"], job.payload["cooldown_days"])
    query = select(Manager).where(
        Manager.is_internal.is_(True), Manager.first_game_start_at >= coverage
    )
    if job.payload.get("after"):
        query = query.where(Manager.id > UUID(job.payload["after"]))
    managers = (
        await session.scalars(query.order_by(Manager.id).limit(BATCH).with_for_update())
    ).all()
    for manager in managers:
        chosen = await candidate(session, manager, at, coverage)
        if chosen is None:
            continue
        actor = Actor(
            manager.id,
            manager.telegram_id,
            None,
            uuid5(manager.id, f"feedback:{chosen.kind}:v1"),
            at,
        )
        try:
            prompt = await open_prompt(
                session, actor, slug=CAMPAIGNS[chosen.kind], settings=settings, at=at
            )
        except (CooldownActive, PermissionDenied):
            continue
        if not prompt.created:
            continue
        row = await session.get(FeedbackResponse, prompt.response_id)
        assert row is not None
        row.trigger_kind, row.ownership_id, row.fixture_id = (
            chosen.kind,
            chosen.ownership_id,
            chosen.fixture_id,
        )
        row.expires_at = at + timedelta(hours=24)
        await session.execute(
            insert(OutboxMessage)
            .values(
                id=uuid5(row.id, "automatic-outbox:v1"),
                dedupe_key=f"feedback:auto:v1:{row.id}",
                chat_id=manager.telegram_id,
                due_at=at,
                status="pending",
                attempts=0,
                priority=0,
                payload={
                    "version": 1,
                    "kind": "feedback_prompt",
                    "response_id": str(row.id),
                    "manager_id": str(manager.id),
                    "trigger": chosen.kind,
                    "target": "calendar"
                    if chosen.fixture_id
                    else "lineup"
                    if chosen.ownership_id
                    else "claim",
                },
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
    job.payload = {**job.payload, "after": str(managers[-1].id) if len(managers) == BATCH else None}
    job.due_at = at + timedelta(minutes=15)


async def deliverable(
    session: AsyncSession, response: FeedbackResponse, manager: Manager, at: datetime
) -> bool:
    root = await session.get(Job, SCAN_ID, populate_existing=True)
    if root is None or root.status != "pending" or not root.payload.get("enabled"):
        return False
    if (
        response.status != "prompted"
        or response.delivered_at is not None
        or response.expires_at is None
        or response.expires_at <= at
    ):
        return False
    # A manager who returned after enqueue should receive their game response,
    # not an obsolete automatic question.
    action = await session.scalar(
        select(GameActionReceipt.operation_id)
        .where(
            GameActionReceipt.manager_id == manager.id,
            GameActionReceipt.occurred_at > response.prompted_at,
        )
        .limit(1)
    )
    if action is not None:
        return False
    current = await candidate(
        session, manager, at, datetime.fromisoformat(root.payload["coverage_at"])
    )
    return current == Candidate(
        response.trigger_kind or "", response.ownership_id, response.fixture_id
    )
