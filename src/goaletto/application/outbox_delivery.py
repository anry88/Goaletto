"""Leased outbox delivery. Network calls run after the caller commits.

A durable board lease serializes each fixture/ownership stream. A unique claim
token and attempt fence protect completion from expired senders. Telegram send
and PostgreSQL commit cannot be atomic; an uncertain send can still duplicate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from html import escape
from typing import Any, Protocol
from uuid import UUID, uuid5

from sqlalchemy import case, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import JourneyEvent, Origin, Reason, Surface, require_utc
from goaletto.application.match_commentary import commentary_html
from goaletto.infrastructure.analytics import CoverageNotStarted, record_event
from goaletto.infrastructure.models import (
    AnalyticsManager,
    Club,
    Fixture,
    Manager,
    OutboxBoard,
    OutboxDeliveryAttempt,
    OutboxMessage,
    OwnershipPeriod,
)

SUPPORTED_VERSION = 1
SUPPORTED_KINDS = frozenset(
    {"fixture_reminder", "match_board", "match_result", "ownership_reminder"}
)
LEASE_SECONDS = 120.0
MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 600.0
PRIORITY_BY_KIND = {
    "match_result": 100,
    "match_board": 50,
    "fixture_reminder": 30,
    "ownership_reminder": 10,
}


class Decision(StrEnum):
    DELIVER = "deliver"
    SKIP_STALE = "skip_stale"
    SKIP_UNAVAILABLE = "skip_unavailable"


class Outcome(StrEnum):
    SENT = "sent"
    EDITED = "edited"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    CHAT_UNAVAILABLE = "chat_unavailable"
    INVALID = "invalid_request"


class LeaseLost(ValueError):
    """Completion belongs to an expired/replaced claim; never change its state."""


@dataclass(frozen=True)
class SendResult:
    message_id: int
    duplicate: bool = False


class RateLimited(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__("rate limited")
        self.retry_after = retry_after


class Blocked(Exception):
    pass


class ChatUnavailable(Exception):
    pass


class MessageMissing(Exception):
    """The chat exists, but the message being edited was deleted."""


class InvalidRequest(Exception):
    """Telegram explicitly rejected the request; it was not delivered."""


class TransportTimeout(Exception):
    pass


class UnknownOutcome(Exception):
    """The request may have reached Telegram; never count it as delivered."""


class TelegramTransport(Protocol):
    async def send_text(self, chat_id: int, text: str) -> SendResult: ...
    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None: ...


def priority_for(payload: dict[str, Any]) -> int:
    kind = payload.get("kind")
    return PRIORITY_BY_KIND.get(kind, 0) if isinstance(kind, str) else 0


def _payload_uuid(payload: dict[str, Any], key: str) -> UUID | None:
    value = payload.get(key)
    try:
        return UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None


def live_board_key_for(payload: dict[str, Any]) -> str | None:
    if payload.get("kind") not in {"match_board", "match_result"}:
        return None
    fixture = _payload_uuid(payload, "fixture_id")
    ownership = _payload_uuid(payload, "ownership_id")
    return f"live:v1:{fixture}:{ownership}" if fixture and ownership else None


def board_revision(payload: dict[str, Any]) -> int:
    tick, phase = payload.get("tick"), payload.get("phase")
    if type(tick) is not int or not 0 <= tick <= 30:
        raise ValueError("Invalid board tick")
    phases = {"first_half": 0, "half_time": 1, "second_half": 2}
    if payload.get("kind") == "match_result":
        if phase not in {"full_time", "abandoned"}:
            raise ValueError("Invalid result phase")
        return 1000
    if not isinstance(phase, str) or phase not in phases:
        raise ValueError("Invalid live phase")
    return tick * 10 + phases[phase]


def valid_payload(payload: dict[str, Any]) -> bool:
    kind = payload.get("kind")
    if (
        type(payload.get("version")) is not int
        or payload["version"] != SUPPORTED_VERSION
        or not isinstance(kind, str)
        or kind not in SUPPORTED_KINDS
        or _payload_uuid(payload, "ownership_id") is None
    ):
        return False
    if kind == "ownership_reminder":
        try:
            stamp = payload.get("last_action_at")
            if not isinstance(stamp, str):
                return False
            require_utc(datetime.fromisoformat(stamp))
        except ValueError:
            return False
        return _payload_uuid(payload, "club_id") is not None
    if _payload_uuid(payload, "fixture_id") is None:
        return False
    if kind == "fixture_reminder":
        return True
    if _payload_uuid(payload, "match_id") is None:
        return False
    if "game_minute" in payload and (
        type(payload["game_minute"]) is not int or not 0 <= payload["game_minute"] <= 90
    ):
        return False
    if "commentary" in payload and (
        not isinstance(payload["commentary"], list)
        or len(payload["commentary"]) > 12
        or any(not isinstance(line, str) or len(line) > 180 for line in payload["commentary"])
    ):
        return False
    for key in ("shots", "on_target"):
        if key in payload and (
            not isinstance(payload[key], list)
            or len(payload[key]) != 2
            or any(type(n) is not int or n < 0 for n in payload[key])
        ):
            return False
    try:
        board_revision(payload)
    except ValueError:
        return False
    return all(isinstance(payload.get(k), str) for k in ("home", "away")) and all(
        type(payload.get(k)) is int and payload[k] >= 0 for k in ("home_goals", "away_goals")
    )


def render_text(payload: dict[str, Any]) -> str:
    if not valid_payload(payload):
        raise ValueError("Unsupported notification")
    kind = payload["kind"]
    if kind in {"match_board", "match_result"}:
        # The transport uses Telegram HTML. Names are data, never markup.
        home, away = escape(payload["home"][:80]), escape(payload["away"][:80])
        score = f"{home} {payload['home_goals']}:{payload['away_goals']} {away}"
        commentary = (
            "\n\nПоследние события:\n" + commentary_html(payload.get("commentary", []))
            if payload.get("commentary")
            else ""
        )
        stats = (
            (
                f"\nУдары: {payload['shots'][0]} / {payload['shots'][1]}"
                f" · В створ: {payload['on_target'][0]} / {payload['on_target'][1]}"
            )
            if "shots" in payload and "on_target" in payload
            else ""
        )
        if kind == "match_result":
            if payload["phase"] == "abandoned":
                return f"Матч остановлен: {score}. Итоговый результат не присуждён."
            return f"Финальный свисток: {score}.{stats}{commentary}"
        minute = payload.get("game_minute", payload["tick"] * 3)
        suffix = " · Перерыв" if payload["phase"] == "half_time" else ""
        return f"⚽ {minute}′{suffix}\n{score}{stats}{commentary}\n\n/match — управление матчем"
    if kind == "fixture_reminder":
        return "Скоро начинается матч вашего клуба. Проверьте состав перед игрой."
    return "Ваш клуб ждёт вас. Откройте /start, чтобы продолжить управление."


def compute_backoff(attempts: int, retry_after: float | None = None) -> float:
    if type(attempts) is not int or attempts < 1:
        raise ValueError("Positive attempt count required")
    delay = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * 2 ** min(attempts - 1, 8))
    if retry_after is not None:
        if not math.isfinite(retry_after) or retry_after < 0:
            raise ValueError("Invalid retry_after")
        # Telegram's deadline is a lower bound, never clipped to our backoff cap.
        delay = max(delay, retry_after)
    return float(delay)


def _transaction(session: AsyncSession) -> None:
    if not session.in_transaction():
        raise ValueError("Outbox requires a caller-owned transaction")


def _clear_lease(row: OutboxMessage | OutboxBoard) -> None:
    row.leased_until = None
    row.lease_owner = None


async def _expire_attempts(session: AsyncSession, now: datetime, outbox_id: UUID) -> None:
    expired = select(OutboxMessage.id).where(OutboxMessage.leased_until <= now)
    statement = update(OutboxDeliveryAttempt).where(
        OutboxDeliveryAttempt.finished_at.is_(None),
        OutboxDeliveryAttempt.outbox_id.in_(expired),
        OutboxDeliveryAttempt.outbox_id == outbox_id,
    )
    await session.execute(
        statement.values(
            outcome=case(
                (OutboxDeliveryAttempt.network_started_at.is_not(None), "unknown"),
                else_="transient",
            ),
            finished_at=now,
        )
    )


async def check_recipient(session: AsyncSession, message: OutboxMessage, now: datetime) -> Decision:
    """Fresh reads immediately before send; no ownership locks across network I/O."""
    _transaction(session)
    require_utc(now)
    payload = message.payload
    if not isinstance(payload, dict) or not valid_payload(payload):
        return Decision.SKIP_STALE
    ownership = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.id == _payload_uuid(payload, "ownership_id"))
        .execution_options(populate_existing=True)
    )
    if ownership is None or ownership.ended_at is not None or ownership.expires_at <= now:
        return Decision.SKIP_STALE
    manager = await session.get(Manager, ownership.manager_id, populate_existing=True)
    club = await session.get(Club, ownership.club_id, populate_existing=True)
    if (
        manager is None
        or club is None
        or manager.telegram_id != message.chat_id
        or club.owner_telegram_id != manager.telegram_id
    ):
        return Decision.SKIP_STALE
    if not manager.bot_available:
        return Decision.SKIP_UNAVAILABLE
    kind = payload["kind"]
    if kind == "ownership_reminder":
        if (
            not manager.reminders_enabled
            or _payload_uuid(payload, "club_id") != ownership.club_id
            or datetime.fromisoformat(payload["last_action_at"]) != ownership.last_game_action_at
        ):
            return Decision.SKIP_STALE
    else:
        fixture = await session.get(
            Fixture, _payload_uuid(payload, "fixture_id"), populate_existing=True
        )
        if fixture is None or ownership.club_id not in (fixture.home_club_id, fixture.away_club_id):
            return Decision.SKIP_STALE
        if kind == "fixture_reminder" and (
            not manager.reminders_enabled
            or fixture.status != "scheduled"
            or fixture.scheduled_start_at <= now
        ):
            return Decision.SKIP_STALE
        if kind == "match_board" and fixture.status not in {"live", "paused"}:
            return Decision.SKIP_STALE
        if kind == "match_result" and fixture.status not in {"finished", "abandoned"}:
            return Decision.SKIP_STALE
    return Decision.DELIVER


async def _board(
    session: AsyncSession, key: str, *, skip_locked: bool = False
) -> OutboxBoard | None:
    row = await session.scalar(
        select(OutboxBoard)
        .where(OutboxBoard.key == key)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    )
    return row if isinstance(row, OutboxBoard) else None


async def claim_outbox(
    session: AsyncSession,
    now: datetime,
    *,
    lease_owner: str,
    lease_seconds: float = LEASE_SECONDS,
    limit: int = 1,
) -> list[OutboxMessage]:
    """Lease up to `limit` independent streams, then commit before preflight/I/O."""
    _transaction(session)
    require_utc(now)
    if not lease_owner or len(lease_owner) > 64:
        raise ValueError("Lease owner must be a short non-empty token")
    if type(limit) is not int or not 1 <= limit <= 25:
        raise ValueError("Claim batch must be bounded 1..25")
    if not math.isfinite(lease_seconds) or not 60 <= lease_seconds <= 300:
        raise ValueError("Lease must be between 60 and 300 seconds")
    busy_board = (
        select(OutboxBoard.key)
        .where(
            OutboxBoard.key == OutboxMessage.live_board_key,
            or_(OutboxBoard.leased_until > now, OutboxBoard.retry_at > now),
        )
        .exists()
    )
    rows = (
        await session.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == "pending",
                OutboxMessage.due_at <= now,
                or_(OutboxMessage.leased_until.is_(None), OutboxMessage.leased_until <= now),
                ~busy_board,
            )
            .order_by(OutboxMessage.priority.desc(), OutboxMessage.due_at, OutboxMessage.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
            .limit(25)
        )
    ).all()
    claimed: list[OutboxMessage] = []
    for row in rows:
        if len(claimed) >= limit:
            break
        if row.leased_until is not None and row.leased_until <= now:
            await _expire_attempts(session, now, row.id)
            row.last_error = "unknown_outcome"
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            row.last_error = row.last_error or "unknown_outcome"
            _clear_lease(row)
            continue
        decision = await check_recipient(session, row, now)
        if decision != Decision.DELIVER:
            row.status, row.last_error = "failed", decision.value
            _clear_lease(row)
            continue
        key = live_board_key_for(row.payload)
        if key is not None:
            await session.execute(
                insert(OutboxBoard)
                .values(
                    key=key,
                    fixture_id=_payload_uuid(row.payload, "fixture_id"),
                    ownership_id=_payload_uuid(row.payload, "ownership_id"),
                    chat_id=row.chat_id,
                )
                .on_conflict_do_nothing(index_elements=["key"])
            )
            board = await _board(session, key, skip_locked=True)
            if (
                board is None
                or (board.leased_until is not None and board.leased_until > now)
                or (board.retry_at is not None and board.retry_at > now)
            ):
                continue
            if board.chat_id != row.chat_id or board_revision(row.payload) <= board.revision:
                row.status, row.last_error = "failed", "superseded"
                _clear_lease(row)
                continue
            board.lease_owner = lease_owner
            board.leased_until = now + timedelta(seconds=lease_seconds)
            board.retry_at = None
            row.live_board_key = key
        row.attempts += 1
        row.lease_owner = lease_owner
        row.leased_until = now + timedelta(seconds=lease_seconds)
        session.add(
            OutboxDeliveryAttempt(
                outbox_id=row.id,
                attempt=row.attempts,
                lease_owner=lease_owner,
                started_at=now,
                outcome="claimed",
            )
        )
        claimed.append(row)
        # Autoflush makes this stream's lease visible to subsequent candidates.
        await session.flush()
    return claimed


async def release_expired_leases(session: AsyncSession, now: datetime) -> int:
    _transaction(session)
    require_utc(now)
    # Lock order matches preflight/finish: outbox -> board -> attempt history.
    rows = (
        await session.scalars(
            select(OutboxMessage)
            .where(OutboxMessage.status == "pending", OutboxMessage.leased_until <= now)
            .order_by(OutboxMessage.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
            .limit(25)
        )
    ).all()
    count = 0
    for row in rows:
        if row.live_board_key is not None:
            board = await _board(session, row.live_board_key, skip_locked=True)
            if board is None:
                continue
            if board.lease_owner == row.lease_owner and board.leased_until == row.leased_until:
                _clear_lease(board)
        await _expire_attempts(session, now, row.id)
        row.last_error = "unknown_outcome"
        _clear_lease(row)
        count += 1
    return count


async def find_live_board_message_id(
    session: AsyncSession, key: str, exclude_id: UUID
) -> int | None:
    _transaction(session)
    board = await session.get(OutboxBoard, key, populate_existing=True)
    return board.telegram_message_id if board else None


async def _leased_row(
    session: AsyncSession, message_id: UUID, now: datetime, lease_owner: str, attempt: int
) -> OutboxMessage:
    _transaction(session)
    require_utc(now)
    row = await session.scalar(
        select(OutboxMessage)
        .where(OutboxMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ValueError("Unknown outbox message")
    if row.status != "pending":
        return row  # Idempotent repeat: terminal receipts cannot be overwritten.
    if (
        row.lease_owner != lease_owner
        or not lease_owner
        or row.attempts != attempt
        or row.leased_until is None
        or row.leased_until <= now
    ):
        raise LeaseLost("Outbox claim expired or replaced")
    return row


async def _leased_board(
    session: AsyncSession, row: OutboxMessage, now: datetime
) -> OutboxBoard | None:
    if row.live_board_key is None:
        return None
    board = await _board(session, row.live_board_key)
    if (
        board is None
        or board.lease_owner != row.lease_owner
        or board.leased_until != row.leased_until
        or board.leased_until is None
        or board.leased_until <= now
    ):
        raise LeaseLost("Board claim expired or replaced")
    return board


async def _attempt(session: AsyncSession, row: OutboxMessage) -> OutboxDeliveryAttempt:
    attempt = await session.get(
        OutboxDeliveryAttempt, (row.id, row.attempts), populate_existing=True
    )
    if attempt is None or attempt.lease_owner != row.lease_owner:
        raise LeaseLost("Delivery attempt does not match claim")
    return attempt


async def _record_delivery_event(
    session: AsyncSession, message: OutboxMessage, now: datetime, outcome: Outcome
) -> None:
    if outcome in {Outcome.BLOCKED, Outcome.CHAT_UNAVAILABLE}:
        name = "bot_unavailable"
        reason = Reason.BLOCKED if outcome == Outcome.BLOCKED else Reason.CHAT_UNAVAILABLE
    elif message.payload["kind"] == "match_result":
        name, reason = "result_delivered", None
    else:
        return
    ownership = await session.get(OwnershipPeriod, _payload_uuid(message.payload, "ownership_id"))
    if ownership is None:
        return
    analytics_id = await session.scalar(
        select(AnalyticsManager.id).where(AnalyticsManager.manager_id == ownership.manager_id)
    )
    if analytics_id is None:
        return
    operation = uuid5(message.id, f"delivery:v1:{name}")
    try:
        await record_event(
            session,
            JourneyEvent(
                event_id=uuid5(operation, "event"),
                name=name,
                occurred_at=now,
                recorded_at=now,
                origin=Origin.SYSTEM,
                operation_id=operation,
                effect_number=0,
                surface=Surface.DELIVERY,
                analytics_manager_id=analytics_id,
                outbox_id=message.id,
                fixture_id=_payload_uuid(message.payload, "fixture_id"),
                reason=reason,
            ),
        )
    except CoverageNotStarted:
        pass


async def finish_delivery(
    session: AsyncSession,
    message_id: UUID,
    now: datetime,
    outcome: Outcome,
    *,
    lease_owner: str,
    attempt: int,
    telegram_message_id: int | None = None,
    retry_after: float | None = None,
) -> OutboxMessage:
    row = await _leased_row(session, message_id, now, lease_owner, attempt)
    if row.status != "pending":
        return row
    board = await _leased_board(session, row, now)
    delivery_attempt = await _attempt(session, row)
    delivery_attempt.finished_at = now
    delivery_attempt.outcome = outcome.value
    if outcome in {Outcome.SENT, Outcome.EDITED, Outcome.DUPLICATE}:
        if type(telegram_message_id) is not int or telegram_message_id <= 0:
            raise ValueError("Successful delivery needs a Telegram message id")
        row.status, row.last_error = "sent", None
        row.telegram_message_id = telegram_message_id
        if board is not None:
            board.telegram_message_id = telegram_message_id
            board.revision = board_revision(row.payload)
            board.retry_at = None
        await _record_delivery_event(session, row, now, outcome)
    elif outcome in {Outcome.RATE_LIMITED, Outcome.TRANSIENT, Outcome.UNKNOWN}:
        error = "unknown_outcome" if outcome == Outcome.UNKNOWN else outcome.value
        deadline = now + timedelta(seconds=compute_backoff(row.attempts, retry_after))
        if board is not None:
            board.retry_at = deadline
        if row.attempts >= MAX_ATTEMPTS:
            row.status, row.last_error = "failed", error
        else:
            row.due_at = deadline
            row.last_error = error
    elif outcome in {Outcome.BLOCKED, Outcome.CHAT_UNAVAILABLE, Outcome.INVALID}:
        row.status, row.last_error = "failed", outcome.value
        if outcome != Outcome.INVALID:
            manager = await session.scalar(
                select(Manager)
                .where(Manager.telegram_id == row.chat_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if manager is not None:
                dispatched_at = delivery_attempt.network_started_at or delivery_attempt.started_at
                # A newer inbound /start proves availability after this attempt started.
                if manager.bot_confirmed_at is None or manager.bot_confirmed_at <= dispatched_at:
                    manager.bot_available = False
            await _record_delivery_event(session, row, now, outcome)
    else:
        raise ValueError("Unknown delivery outcome")
    _clear_lease(row)
    if board is not None:
        _clear_lease(board)
    return row


async def skip_stale(
    session: AsyncSession,
    message_id: UUID,
    now: datetime,
    reason: Decision,
    *,
    lease_owner: str,
    attempt: int,
) -> OutboxMessage:
    if reason == Decision.DELIVER:
        raise ValueError("Cannot skip a deliver decision")
    row = await _leased_row(session, message_id, now, lease_owner, attempt)
    if row.status == "pending":
        board = await _leased_board(session, row, now)
        delivery_attempt = await _attempt(session, row)
        delivery_attempt.finished_at = now
        delivery_attempt.outcome = reason.value
        row.status, row.last_error = "failed", reason.value
        _clear_lease(row)
        if board is not None:
            _clear_lease(board)
    return row


async def preflight_delivery(
    session: AsyncSession, message_id: UUID, now: datetime, *, lease_owner: str, attempt: int
) -> tuple[OutboxMessage, int | None] | None:
    """Commit these fresh guards directly before network; skip stale work atomically."""
    row = await _leased_row(session, message_id, now, lease_owner, attempt)
    if row.status != "pending":
        return None
    board = await _leased_board(session, row, now)
    decision = await check_recipient(session, row, now)
    if decision != Decision.DELIVER:
        await skip_stale(
            session, message_id, now, decision, lease_owner=lease_owner, attempt=attempt
        )
        return None
    delivery_attempt = await _attempt(session, row)
    delivery_attempt.network_started_at = now
    delivery_attempt.outcome = "sending"
    return row, board.telegram_message_id if board else None


async def deliver_claimed(
    message: OutboxMessage,
    transport: TelegramTransport,
    now: datetime,
    *,
    existing_board_message_id: int | None = None,
) -> tuple[Outcome, int | None, float | None]:
    """Call after preflight commit, without an open database transaction."""
    require_utc(now)
    try:
        text = render_text(message.payload)
        if existing_board_message_id is not None:
            try:
                await transport.edit_text(message.chat_id, existing_board_message_id, text)
                return Outcome.EDITED, existing_board_message_id, None
            except MessageMissing:
                pass  # Deleted board: send its replacement, keeping the stream lease.
        result = await transport.send_text(message.chat_id, text)
        if type(result.message_id) is not int or result.message_id <= 0:
            raise UnknownOutcome()
        return (
            Outcome.DUPLICATE if result.duplicate else Outcome.SENT,
            result.message_id,
            None,
        )
    except RateLimited as error:
        return Outcome.RATE_LIMITED, None, error.retry_after
    except Blocked:
        return Outcome.BLOCKED, None, None
    except ChatUnavailable:
        return Outcome.CHAT_UNAVAILABLE, None, None
    except (InvalidRequest, ValueError):
        return Outcome.INVALID, None, None
    except (TransportTimeout, UnknownOutcome):
        return Outcome.UNKNOWN, None, None
    except Exception:
        # Includes unexpected edit failures: never escape into the simulation loop.
        return Outcome.TRANSIENT, None, None
