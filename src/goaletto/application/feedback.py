"""Voluntary manual feedback. Internal/test cohort only; no automatic campaigns.

Manual entry never promises delivery: the prompt is stored durably first,
Telegram send happens outside the transaction, and prompt_delivered is recorded
in a separate idempotent transaction only after transport confirmation.
Feedback is explicit analytic activity but never extends the six-month ownership.
Free text lives in feedback_comments only, never in journey envelopes/logs.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import (
    JourneyEvent,
    Origin,
    Reason,
    Surface,
    require_utc,
)
from goaletto.application.game_actions import Actor, PermissionDenied
from goaletto.infrastructure.analytics import CoverageNotStarted, exclude_manager, record_event
from goaletto.infrastructure.models import (
    AnalyticsExclusion,
    AnalyticsManager,
    FeedbackCampaign,
    FeedbackComment,
    FeedbackResponse,
    Manager,
)

MANUAL_CAMPAIGN_SLUG = "manual-v1"
COMMENT_WINDOW = timedelta(minutes=30)
COMMENT_MAX_CHARS = 500
COOLDOWN_MIN_DAYS = 7

REASON_CODES: tuple[str, ...] = (
    "unclear_next_step",
    "difficult_lineup",
    "long_wait",
    "inconvenient_time",
    "uninteresting_game",
    "technical_problem",
    "other",
)

_REASON_TO_ENUM = {
    "unclear_next_step": Reason.UNCLEAR_NEXT_STEP,
    "difficult_lineup": Reason.DIFFICULT_LINEUP,
    "long_wait": Reason.LONG_WAIT,
    "inconvenient_time": Reason.INCONVENIENT_TIME,
    "uninteresting_game": Reason.UNINTERESTING_GAME,
    "technical_problem": Reason.TECHNICAL_FAILURE,
    "other": Reason.OTHER,
}


class FeedbackDisabled(ValueError):
    pass


class CooldownActive(ValueError):
    pass


class CommentExpired(ValueError):
    pass


@dataclass(frozen=True)
class FeedbackSettings:
    enabled: bool
    comment_retention_days: int | None
    cooldown_days: int = 7

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("Feedback enabled must be a boolean")
        if self.comment_retention_days is not None and (
            type(self.comment_retention_days) is not int
            or not 1 <= self.comment_retention_days <= 730
        ):
            raise ValueError("Comment retention must be 1..730 days when set")
        if (
            type(self.cooldown_days) is not int
            or not COOLDOWN_MIN_DAYS <= self.cooldown_days <= 730
        ):
            raise ValueError("Cooldown must be at least 7 days")
        if self.enabled and self.comment_retention_days is None:
            raise ValueError("Feedback collection needs explicit comment retention")


@dataclass(frozen=True)
class PromptOutcome:
    response_id: UUID
    campaign_id: UUID
    created: bool
    duplicate: bool
    status: str


@dataclass(frozen=True)
class AnswerOutcome:
    applied: bool
    duplicate: bool
    status: str
    reason: str | None


@dataclass(frozen=True)
class CommentOutcome:
    applied: bool
    duplicate: bool


def _check_session(session: AsyncSession) -> None:
    if not session.in_transaction():
        raise ValueError("Feedback needs a transaction")


def _check_slug(slug: str) -> None:
    if type(slug) is not str or not re.fullmatch(r"[a-z0-9_-]{1,64}", slug):
        raise ValueError("Invalid feedback campaign")


async def _locked_manager(session: AsyncSession, actor: Actor) -> Manager:
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == actor.manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None or manager.telegram_id != actor.telegram_id:
        raise PermissionDenied("Manager identity does not match the actor")
    return manager


async def _check_eligibility(session: AsyncSession, manager: Manager) -> None:
    exclusion = await session.scalar(
        select(AnalyticsExclusion).where(AnalyticsExclusion.manager_id == manager.id)
    )
    if exclusion is not None and exclusion.kind in {"opt_out", "operator"}:
        raise PermissionDenied("Feedback is not available after opt-out")
    if not bool(manager.is_internal):
        raise PermissionDenied("Feedback is limited to the internal test cohort")
    if not bool(manager.bot_available):
        raise PermissionDenied("Feedback is unavailable while delivery is blocked")


def _require_collection(settings: FeedbackSettings) -> None:
    if not settings.enabled or settings.comment_retention_days is None:
        raise FeedbackDisabled("Feedback collection is disabled")


async def ensure_campaign(session: AsyncSession, slug: str, at: datetime) -> FeedbackCampaign:
    _check_session(session)
    require_utc(at)
    _check_slug(slug)
    existing = await session.scalar(select(FeedbackCampaign).where(FeedbackCampaign.slug == slug))
    if existing is not None:
        return existing
    campaign_id = uuid5(UUID(int=0), f"feedback-campaign:v1:{slug}")
    await session.execute(
        insert(FeedbackCampaign)
        .values(id=campaign_id, slug=slug, created_at=at)
        .on_conflict_do_nothing(index_elements=["slug"])
    )
    created = await session.scalar(select(FeedbackCampaign).where(FeedbackCampaign.slug == slug))
    if created is None:
        raise ValueError("Feedback campaign was not persisted")
    return created


async def _last_prompt_at(session: AsyncSession, manager_id: UUID) -> datetime | None:
    row = await session.scalar(
        select(FeedbackResponse.prompted_at)
        .where(FeedbackResponse.manager_id == manager_id)
        .order_by(FeedbackResponse.prompted_at.desc())
        .limit(1)
    )
    return row if isinstance(row, datetime) else None


async def open_prompt(
    session: AsyncSession,
    actor: Actor,
    *,
    slug: str,
    settings: FeedbackSettings,
    at: datetime,
) -> PromptOutcome:
    _check_session(session)
    require_utc(at)
    _check_slug(slug)
    if not isinstance(settings, FeedbackSettings):
        raise ValueError("Invalid feedback settings")
    _require_collection(settings)
    manager = await _locked_manager(session, actor)
    await _check_eligibility(session, manager)
    campaign = await ensure_campaign(session, slug, at)
    existing = await session.scalar(
        select(FeedbackResponse)
        .where(
            FeedbackResponse.manager_id == manager.id,
            FeedbackResponse.campaign_id == campaign.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return PromptOutcome(
            response_id=existing.id,
            campaign_id=campaign.id,
            created=False,
            duplicate=True,
            status=existing.status,
        )
    last = await _last_prompt_at(session, manager.id)
    if last is not None and at - last < timedelta(days=settings.cooldown_days):
        raise CooldownActive("Feedback cooldown is active")
    response_id = uuid5(manager.id, f"feedback:v1:{campaign.id}")
    statement = (
        insert(FeedbackResponse)
        .values(
            id=response_id,
            campaign_id=campaign.id,
            manager_id=manager.id,
            operation_id=actor.operation_id,
            status="prompted",
            reason=None,
            prompted_at=at,
            responded_at=None,
            comment_due_at=None,
        )
        .on_conflict_do_nothing(index_elements=["manager_id", "campaign_id"])
        .returning(FeedbackResponse.id)
    )
    inserted = await session.scalar(statement)
    current = await session.scalar(
        select(FeedbackResponse)
        .where(
            FeedbackResponse.manager_id == manager.id,
            FeedbackResponse.campaign_id == campaign.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise ValueError("Feedback prompt was not persisted")
    created = inserted is not None
    return PromptOutcome(
        response_id=current.id,
        campaign_id=campaign.id,
        created=created,
        duplicate=not created,
        status=current.status,
    )


async def _locked_response(
    session: AsyncSession, actor: Actor, response_id: UUID
) -> FeedbackResponse:
    if not isinstance(response_id, UUID):
        raise ValueError("Invalid feedback response")
    response = await session.scalar(
        select(FeedbackResponse)
        .where(FeedbackResponse.id == response_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if response is None or response.manager_id != actor.manager_id:
        raise PermissionDenied("Feedback button is stale or foreign")
    return response


async def _record_user_event(
    session: AsyncSession,
    *,
    actor: Actor,
    name: str,
    campaign_id: UUID,
    operation: UUID,
    reason: Reason | None,
    at: datetime,
) -> None:
    if actor.analytics_id is None:
        return
    try:
        await record_event(
            session,
            JourneyEvent(
                event_id=uuid5(operation, "event"),
                name=name,
                origin=Origin.USER,
                operation_id=operation,
                effect_number=0,
                surface=Surface.FEEDBACK,
                occurred_at=at,
                recorded_at=at,
                analytics_manager_id=actor.analytics_id,
                session_id=uuid5(operation, "session-proposal"),
                feedback_campaign_id=campaign_id,
                reason=reason,
            ),
        )
    except CoverageNotStarted:
        return


async def answer_with_reason(
    session: AsyncSession,
    actor: Actor,
    *,
    response_id: UUID,
    reason_code: str,
    at: datetime,
) -> AnswerOutcome:
    _check_session(session)
    require_utc(at)
    if reason_code not in _REASON_TO_ENUM:
        raise ValueError("Unknown feedback reason")
    manager = await _locked_manager(session, actor)
    await _check_eligibility(session, manager)
    response = await _locked_response(session, actor, response_id)
    if response.status in {"skipped", "opted_out"}:
        return AnswerOutcome(applied=False, duplicate=True, status=response.status, reason=None)
    if response.status == "answered":
        return AnswerOutcome(
            applied=False, duplicate=True, status=response.status, reason=response.reason
        )
    response.status = "answered"
    response.reason = reason_code
    response.responded_at = at
    response.comment_due_at = at + COMMENT_WINDOW
    await session.flush()
    await _record_user_event(
        session,
        actor=actor,
        name="feedback_answered",
        campaign_id=response.campaign_id,
        operation=uuid5(response.id, "answer:v1"),
        reason=_REASON_TO_ENUM[reason_code],
        at=at,
    )
    return AnswerOutcome(applied=True, duplicate=False, status="answered", reason=reason_code)


async def skip_prompt(
    session: AsyncSession, actor: Actor, *, response_id: UUID, at: datetime
) -> AnswerOutcome:
    _check_session(session)
    require_utc(at)
    manager = await _locked_manager(session, actor)
    await _check_eligibility(session, manager)
    response = await _locked_response(session, actor, response_id)
    if response.status in {"skipped", "opted_out", "answered"}:
        return AnswerOutcome(
            applied=False, duplicate=True, status=response.status, reason=response.reason
        )
    response.status = "skipped"
    response.responded_at = at
    response.comment_due_at = None
    await session.flush()
    await _record_user_event(
        session,
        actor=actor,
        name="feedback_skipped",
        campaign_id=response.campaign_id,
        operation=uuid5(response.id, "skip:v1"),
        reason=None,
        at=at,
    )
    return AnswerOutcome(applied=True, duplicate=False, status="skipped", reason=None)


async def opt_out(
    session: AsyncSession, actor: Actor, *, response_id: UUID, at: datetime
) -> AnswerOutcome:
    _check_session(session)
    require_utc(at)
    manager = await _locked_manager(session, actor)
    # Opt-out is allowed even when a prior opt-out tombstone exists for idempotency.
    if not bool(manager.is_internal):
        raise PermissionDenied("Feedback is limited to the internal test cohort")
    response = await _locked_response(session, actor, response_id)
    if response.status == "opted_out":
        return AnswerOutcome(applied=False, duplicate=True, status="opted_out", reason=None)
    if response.status in {"skipped", "answered"}:
        # Terminal answers stay terminal; opting out still erases text and mapping.
        response.status = "opted_out"
        response.reason = None
        response.responded_at = response.responded_at or at
        response.comment_due_at = None
        await session.flush()
    else:
        response.status = "opted_out"
        response.reason = None
        response.responded_at = at
        response.comment_due_at = None
        await session.flush()
        await _record_user_event(
            session,
            actor=actor,
            name="feedback_skipped",
            campaign_id=response.campaign_id,
            operation=uuid5(response.id, "skip:v1"),
            reason=None,
            at=at,
        )
    owned = select(FeedbackResponse.id).where(FeedbackResponse.manager_id == manager.id)
    await session.execute(delete(FeedbackComment).where(FeedbackComment.response_id.in_(owned)))
    await exclude_manager(session, manager.id, "opt_out", at)
    return AnswerOutcome(applied=True, duplicate=False, status="opted_out", reason=None)


async def find_open_comment(
    session: AsyncSession, manager_id: UUID, at: datetime
) -> FeedbackResponse | None:
    _check_session(session)
    require_utc(at)
    if not isinstance(manager_id, UUID):
        raise ValueError("Invalid manager identity")
    candidates = (
        await session.scalars(
            select(FeedbackResponse)
            .where(
                FeedbackResponse.manager_id == manager_id,
                FeedbackResponse.status == "answered",
                FeedbackResponse.comment_due_at.is_not(None),
                FeedbackResponse.comment_due_at >= at,
                FeedbackResponse.responded_at.is_not(None),
                FeedbackResponse.responded_at <= at,
            )
            .order_by(FeedbackResponse.responded_at.desc())
        )
    ).all()
    for response in candidates:
        existing = await session.get(FeedbackComment, response.id)
        if existing is None:
            return response
    return None


async def save_comment(
    session: AsyncSession,
    actor: Actor,
    *,
    response_id: UUID,
    text: str,
    at: datetime,
) -> CommentOutcome:
    _check_session(session)
    require_utc(at)
    if type(text) is not str:
        raise ValueError("Invalid feedback comment")
    cleaned = text.strip()
    if not 1 <= len(cleaned) <= COMMENT_MAX_CHARS or len(text) > COMMENT_MAX_CHARS:
        raise ValueError("Comment must be 1..500 characters")
    manager = await _locked_manager(session, actor)
    await _check_eligibility(session, manager)
    response = await _locked_response(session, actor, response_id)
    if response.status != "answered" or response.comment_due_at is None:
        raise PermissionDenied("Comments follow an explicit reason choice")
    if response.responded_at is None or not (
        response.responded_at <= at <= response.comment_due_at
    ):
        raise CommentExpired("Comment window has closed")
    existing = await session.get(FeedbackComment, response.id)
    if existing is not None:
        return CommentOutcome(applied=False, duplicate=True)
    session.add(FeedbackComment(response_id=response.id, created_at=at, text=cleaned))
    await session.flush()
    return CommentOutcome(applied=True, duplicate=False)


async def record_prompt_delivered(
    session: AsyncSession, *, response_id: UUID, outbox_id: UUID, at: datetime
) -> bool:
    _check_session(session)
    require_utc(at)
    if not isinstance(response_id, UUID) or not isinstance(outbox_id, UUID):
        raise ValueError("Invalid feedback delivery identity")
    response = await session.scalar(
        select(FeedbackResponse).where(FeedbackResponse.id == response_id)
    )
    if response is None:
        return False
    manager = await session.scalar(
        select(Manager).where(Manager.id == response.manager_id).with_for_update()
    )
    if manager is None or not bool(manager.is_internal) or not bool(manager.bot_available):
        return False
    response = await session.scalar(
        select(FeedbackResponse)
        .where(FeedbackResponse.id == response_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if response is None or response.status != "prompted" or response.delivered_at is not None:
        return False
    exclusion = await session.scalar(
        select(AnalyticsExclusion).where(AnalyticsExclusion.manager_id == response.manager_id)
    )
    if exclusion is not None and exclusion.kind in {"opt_out", "operator"}:
        return False
    response.delivered_at = at
    await session.flush()
    analytics_id = await session.scalar(
        select(AnalyticsManager.id).where(AnalyticsManager.manager_id == response.manager_id)
    )
    if analytics_id is None:
        return True
    operation = uuid5(response.id, "prompt-delivery:v1")
    try:
        return await record_event(
            session,
            JourneyEvent(
                event_id=uuid5(operation, "event"),
                name="feedback_prompt_delivered",
                origin=Origin.SYSTEM,
                operation_id=operation,
                effect_number=0,
                surface=Surface.FEEDBACK,
                occurred_at=at,
                recorded_at=at,
                analytics_manager_id=analytics_id,
                outbox_id=outbox_id,
                feedback_campaign_id=response.campaign_id,
            ),
        )
    except CoverageNotStarted:
        return False


async def cleanup_comments(session: AsyncSession, *, at: datetime, retention_days: int) -> int:
    _check_session(session)
    require_utc(at)
    if type(retention_days) is not int or not 1 <= retention_days <= 730:
        raise ValueError("Invalid comment retention")
    cutoff = at - timedelta(days=retention_days)
    result = await session.execute(
        delete(FeedbackComment).where(FeedbackComment.created_at < cutoff)
    )
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount or 0)


def prompt_outbox_id(response_id: UUID) -> UUID:
    if not isinstance(response_id, UUID):
        raise ValueError("Invalid feedback response")
    return uuid5(response_id, "prompt-outbox:v1")


async def skip_comment(
    session: AsyncSession, actor: Actor, *, response_id: UUID, at: datetime
) -> bool:
    _check_session(session)
    require_utc(at)
    manager = await _locked_manager(session, actor)
    await _check_eligibility(session, manager)
    response = await _locked_response(session, actor, response_id)
    if response.status != "answered":
        raise PermissionDenied("No comment session")
    if response.comment_due_at is None:
        return False
    response.comment_due_at = None
    await session.flush()
    return True


async def offer_feedback(session: AsyncSession, actor: Actor, at: datetime) -> bool:
    manager = await session.get(Manager, actor.manager_id)
    if manager is None or not manager.is_internal or not manager.bot_available:
        return False
    exclusion = await session.get(AnalyticsExclusion, manager.id)
    if exclusion is not None and exclusion.kind in {"opt_out", "operator"}:
        return False
    campaign_id = uuid5(UUID(int=0), f"feedback-campaign:v1:{MANUAL_CAMPAIGN_SLUG}")
    previous = await session.scalar(
        select(FeedbackResponse.id).where(
            FeedbackResponse.manager_id == manager.id,
            FeedbackResponse.campaign_id == campaign_id,
        )
    )
    return previous is None


async def schedule_comment_cleanup(
    session: AsyncSession, *, at: datetime, retention_days: int
) -> None:
    from goaletto.infrastructure.models import Job

    _check_session(session)
    require_utc(at)
    if type(retention_days) is not int or not 1 <= retention_days <= 730:
        raise ValueError("Invalid comment retention")
    await session.execute(
        insert(Job)
        .values(
            dedupe_key="feedback.cleanup:v1",
            kind="feedback.cleanup",
            payload={"version": 1, "retention_days": retention_days},
            due_at=at,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_update(
            index_elements=["dedupe_key"],
            set_={
                "payload": {"version": 1, "retention_days": retention_days},
                "status": "pending",
                "last_error": None,
            },
        )
    )
