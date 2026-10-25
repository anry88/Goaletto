"""Leased outbox delivery. Network calls run after the caller commits.

A durable board lease serializes each fixture/ownership stream. A unique claim
token and attempt fence protect completion from expired senders. Telegram send
and PostgreSQL commit cannot be atomic; an uncertain send can still duplicate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from html import escape
from typing import Any, Protocol, cast
from uuid import UUID, uuid5

from sqlalchemy import case, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import JourneyEvent, Origin, Reason, Surface, require_utc
from goaletto.application.match_commentary import commentary_html
from goaletto.domain.kickoff import local_datetime
from goaletto.domain.names import latin_name
from goaletto.infrastructure.analytics import CoverageNotStarted, record_event
from goaletto.infrastructure.models import (
    AnalyticsManager,
    Club,
    Fixture,
    Manager,
    Match,
    OutboxBoard,
    OutboxDeliveryAttempt,
    OutboxMessage,
    OwnershipPeriod,
)
from goaletto.presentation.i18n import locale, tr

SUPPORTED_VERSION = 1
SUPPORTED_KINDS = frozenset(
    {
        "fixture_reminder",
        "match_board",
        "match_result",
        "ownership_reminder",
        "match_recovery",
        "market_notice",
        "scout_report",
        "staff_career",
        "friendly_invitation",
        "training_cancelled",
        "player_lifecycle",
        "staff_lifecycle",
        "feedback_prompt",
        "bankruptcy",
        "payment_notice",
    }
)
LEASE_SECONDS = 120.0
MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_CAP_SECONDS = 600.0
PRIORITY_BY_KIND = {
    "match_result": 100,
    "bankruptcy": 100,
    "friendly_invitation": 70,
    "market_notice": 70,
    "scout_report": 70,
    "staff_career": 80,
    "training_cancelled": 70,
    "player_lifecycle": 80,
    "staff_lifecycle": 80,
    "match_recovery": 90,
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
    # Results are standalone notifications, including for owners who never watched.
    if payload.get("kind") != "match_board":
        return None
    fixture = _payload_uuid(payload, "fixture_id")
    ownership = _payload_uuid(payload, "ownership_id")
    return f"live:v1:{fixture}:{ownership}" if fixture and ownership else None


def board_revision(payload: dict[str, Any]) -> int:
    tick, phase = payload.get("tick"), payload.get("phase")
    if type(tick) is not int or not 0 <= tick <= 40:
        raise ValueError("Invalid board tick")
    kicks = payload.get("penalty_kicks", 0)
    if type(kicks) is not int or kicks < 0:
        raise ValueError("Invalid penalty revision")
    phases = {
        "first_half": 0,
        "half_time": 1,
        "second_half": 2,
        "extra_break": 3,
        "extra_first_half": 4,
        "extra_half_time": 5,
        "extra_second_half": 6,
        "penalties": 7,
    }
    if payload.get("kind") == "match_result":
        if phase not in {"full_time", "abandoned"}:
            raise ValueError("Invalid result phase")
        return 1000 + kicks * 10
    if not isinstance(phase, str) or phase not in phases:
        raise ValueError("Invalid live phase")
    return tick * 10 + phases[phase] + kicks * 10


def valid_payload(payload: dict[str, Any]) -> bool:
    kind = payload.get("kind")
    if kind == "payment_notice":
        return (
            payload.get("version") == 1
            and type(payload.get("version")) is int
            and _payload_uuid(payload, "payment_id") is not None
            and isinstance(payload.get("body"), str)
            and 0 < len(payload["body"]) <= 3500
            and (
                payload.get("request_id") is None
                or _payload_uuid(payload, "request_id") is not None
            )
        )
    if kind == "bankruptcy":
        return (
            type(payload.get("version")) is int
            and payload.get("version") == 1
            and _payload_uuid(payload, "ownership_id") is not None
            and _payload_uuid(payload, "receipt_id") is not None
            and isinstance(payload.get("club_name"), str)
            and 0 < len(payload["club_name"]) <= 80
            and type(payload.get("balance")) is int
            and -(2**63) < payload["balance"] < 0
            and type(payload.get("assets")) is int
            and 0 <= payload["assets"] < 2**63
        )
    if kind == "feedback_prompt":
        return (
            set(payload) == {"version", "kind", "response_id", "manager_id", "trigger", "target"}
            and type(payload.get("version")) is int
            and payload["version"] == SUPPORTED_VERSION
            and _payload_uuid(payload, "response_id") is not None
            and _payload_uuid(payload, "manager_id") is not None
            and payload.get("trigger") in {"entry_help", "missed_match"}
            and payload.get("target") in {"claim", "lineup", "calendar"}
        )
    if (
        type(payload.get("version")) is not int
        or payload["version"] != SUPPORTED_VERSION
        or not isinstance(kind, str)
        or kind not in SUPPORTED_KINDS
        or _payload_uuid(payload, "ownership_id") is None
    ):
        return False
    if kind in {"player_lifecycle", "staff_lifecycle"}:
        return (
            all(
                _payload_uuid(payload, k) is not None for k in ("person_id", "season_id", "club_id")
            )
            and payload.get("action") in ("last_season", "retired")
            and isinstance(payload.get("player_name"), str)
            and 0 < len(payload["player_name"]) <= 120
        )
    if kind == "friendly_invitation":
        try:
            day = date.fromisoformat(str(payload.get("day", "")))
        except ValueError:
            return False
        return (
            day.isoformat() == payload.get("day")
            and all(_payload_uuid(payload, k) is not None for k in ("invitation_id", "club_id"))
            and isinstance(payload.get("home"), str)
            and 0 < len(payload["home"]) <= 80
        )
    if kind == "training_cancelled":
        try:
            day = date.fromisoformat(str(payload.get("day", "")))
        except ValueError:
            return False
        return day.isoformat() == payload.get("day") and all(
            _payload_uuid(payload, k) is not None for k in ("plan_id", "club_id")
        )
    if kind == "staff_career":
        return (
            _payload_uuid(payload, "person_id") is not None
            and _payload_uuid(payload, "club_id") is not None
            and isinstance(payload.get("player_name"), str)
            and 0 < len(payload["player_name"]) <= 120
        )
    if kind == "scout_report":
        return (
            _payload_uuid(payload, "mission_id") is not None
            and _payload_uuid(payload, "club_id") is not None
            and type(payload.get("count")) is int
            and 1 <= payload["count"] <= 4
        )
    if kind == "market_notice":
        return (
            _payload_uuid(payload, "auction_id") is not None
            and _payload_uuid(payload, "club_id") is not None
            and _payload_uuid(payload, "person_id") is not None
            and isinstance(payload.get("event"), str)
            and payload.get("event") in {"bid", "outbid", "sold", "bought", "cancelled", "expired"}
            and isinstance(payload.get("player_name"), str)
            and len(payload["player_name"]) <= 120
            and type(payload.get("amount")) is int
            and 0 <= payload["amount"] < 2**63
            and type(payload.get("weekly_rate")) is int
            and 1 <= payload["weekly_rate"] <= 200000
            and (payload.get("bid_id") is None or _payload_uuid(payload, "bid_id") is not None)
            and (
                payload.get("event") not in {"bid", "outbid", "sold", "bought"}
                or payload.get("bid_id") is not None
                and payload["amount"] >= 100000
            )
        )
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
    if kind == "match_recovery":
        try:
            deadline = payload.get("resume_deadline")
            if not isinstance(deadline, str):
                return False
            require_utc(datetime.fromisoformat(deadline))
        except ValueError:
            return False
        return (
            type(payload.get("tick")) is int
            and 0 <= payload["tick"] <= 40
            and type(payload.get("game_minute")) is int
            and 0 <= payload["game_minute"] <= 120
        )
    if "game_minute" in payload and (
        type(payload["game_minute"]) is not int or not 0 <= payload["game_minute"] <= 120
    ):
        return False
    if "commentary_by_locale" in payload:
        timelines = payload["commentary_by_locale"]
        if (
            not isinstance(timelines, dict)
            or set(timelines) != {"ru", "en"}
            or any(
                not isinstance(lines, list)
                or len(lines) > 12
                or any(not isinstance(line, str) or len(line) > 180 for line in lines)
                for lines in timelines.values()
            )
        ):
            return False
    if "commentary" in payload and (
        not isinstance(payload["commentary"], list)
        or len(payload["commentary"]) > 12
        or any(not isinstance(line, str) or len(line) > 180 for line in payload["commentary"])
    ):
        return False
    if "tie_winner_name" in payload and (
        not isinstance(payload["tie_winner_name"], str)
        or not 1 <= len(payload["tie_winner_name"]) <= 80
    ):
        return False
    if "player_of_match_name" in payload and (
        not isinstance(payload["player_of_match_name"], str)
        or not 1 <= len(payload["player_of_match_name"]) <= 120
    ):
        return False
    for key in ("shots", "on_target", "penalties", "aggregate"):
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


def render_text(payload: dict[str, Any], *, timezone_name: str = "UTC") -> str:
    if not valid_payload(payload):
        raise ValueError("Unsupported notification")
    kind = payload["kind"]
    if kind == "payment_notice":
        body = escape(str(payload["body"]))
        # Telegram renders these as tap-to-copy text, including admin request IDs.
        for field in ("payment_id", "request_id"):
            if payload.get(field):
                key = str(payload[field])
                body = body.replace(key, f"<code>{key}</code>")
        return body
    if kind == "bankruptcy":
        return tr(
            "🏦 Клуб {club} объявлен банкротом: долг {debt} превышает стоимость активов {assets}. "
            "Клубом управляет бот. Получить другой клуб можно в следующем сезоне.",
            club=escape(payload["club_name"]),
            debt=f"{-payload['balance']:,}",
            assets=f"{payload['assets']:,}",
        )
    if kind == "feedback_prompt":
        next_step = (
            tr("/start — продолжить выбор клуба.")
            if payload["target"] == "claim"
            else tr("/lineup — подготовить состав к следующему матчу.")
            if payload["target"] == "lineup"
            else tr("/calendar — посмотреть ближайшие игры.")
        )
        return (
            next_step
            + "\n\n"
            + tr(
                "Если хотите, расскажите, что было неудобно: /feedback. "
                "Это добровольно и ни на что не влияет."
            )
        )
    if kind == "friendly_invitation":
        return tr(
            "⚽ {club} приглашает на товарищеский матч {day} UTC. Ответьте в /friendly.",
            club=escape(latin_name(payload["home"])),
            day=payload["day"],
        )
    if kind == "training_cancelled":
        return tr(
            "🏋️ Тренировка {day} UTC отменена: назначен официальный матч. "
            "Выберите свободную дату в /train.",
            day=escape(payload["day"]),
        )
    if kind == "staff_career":
        return tr(
            "🤝 {person} готов продолжить карьеру в штабе вашего клуба. "
            "Лояльность клубу даёт скидку 20% на зарплату. "
            "Условия и ответ в течение 8 дней — /staff.",
            person=escape(latin_name(payload["player_name"])),
        )
    if kind == "scout_report":
        return tr(
            "🔎 Поиск завершён. Найдено игроков: {count}. Итоговый список и цены — /scout.",
            count=payload["count"],
        )
    if kind in {"player_lifecycle", "staff_lifecycle"}:
        if kind == "staff_lifecycle":
            person = escape(latin_name(payload["player_name"]))
            return (
                tr(
                    "⚠️ {person}: объявлен последний рабочий сезон. "
                    "После него сотрудник выйдет на пенсию.",
                    person=person,
                )
                if payload["action"] == "last_season"
                else tr(
                    "🏁 {person} вышел на пенсию. Подберите сотрудника в /staff.", person=person
                )
            )
        player = escape(latin_name(payload["player_name"]))
        if payload["action"] == "last_season":
            return tr(
                "⚠️ {player}: объявлен последний сезон. После него игрок завершит карьеру. "
                "Трансфер недоступен.",
                player=player,
            )
        return tr(
            "🏁 {player} завершил игровую карьеру. Проверьте состав в /lineup.", player=player
        )
    if kind == "market_notice":
        player = escape(latin_name(payload["player_name"]))
        amount = f"{payload['amount']:,}".replace(",", " ")
        wage = f"{payload['weekly_rate']:,}".replace(",", " ")
        event = payload["event"]
        if event == "bid":
            return tr(
                "💰 Новая ставка: {player} · {amount}. Откройте /market.",
                player=player,
                amount=amount,
            )
        if event == "outbid":
            return tr(
                "↗️ Ваша ставка за {player} перебита. Резерв освобождён. Откройте /market.",
                player=player,
            )
        if event == "sold":
            return tr(
                "✅ {player} продан за {amount}. Проверьте /lineup перед матчем.",
                player=player,
                amount=amount,
            )
        if event == "bought":
            return tr(
                "✅ Вы выиграли аукцион: {player} · {amount}. Зарплата: {wage}/период клуба. "
                "Выберите заявку в /lineup.",
                player=player,
                amount=amount,
                wage=wage,
            )
        if event == "expired":
            return tr(
                "⏱ Аукцион за {player} завершился без ставок. Игрок остался в клубе.", player=player
            )
        reason = payload.get("reason")
        if reason == "last_season":
            return tr(
                "⏸ Аукцион за {player} отменён: объявлен последний сезон. Резерв с"
                "тавок освобождён.",
                player=player,
            )
        return tr(
            "⏸ Аукцион за {player} отменён. Резерв ставок освобождён. Откройте /market.",
            player=player,
        )
    if kind == "match_recovery":
        deadline = local_datetime(datetime.fromisoformat(payload["resume_deadline"]), timezone_name)
        return tr(
            "⏸ Матч продолжится с {p0}′ в {p1}. Счёт и принятые команды сохранены. "
            "До этого можно отправить команды через /match.",
            p0=payload["game_minute"],
            p1=deadline,
        )
    if kind in {"match_board", "match_result"}:
        localized = payload.get("commentary_by_locale", {})
        timeline = localized.get(
            locale(), payload.get("commentary", []) if locale() == "ru" else []
        )
        # The transport uses Telegram HTML. Names are data, never markup.
        home, away = (
            escape(latin_name(payload["home"])[:80])
            + (" 👑 VIP" if payload.get("home_vip") else ""),
            escape(latin_name(payload["away"])[:80])
            + (" 👑 VIP" if payload.get("away_vip") else ""),
        )
        score = f"{home} {payload['home_goals']}:{payload['away_goals']} {away}"
        if "aggregate" in payload:
            score += "\n" + tr(
                "Общий счёт: {home}:{away}",
                home=payload["aggregate"][0],
                away=payload["aggregate"][1],
            )
        if "tie_winner_name" in payload:
            score += "\n" + tr(
                "Победитель: {club}", club=escape(latin_name(payload["tie_winner_name"]))
            )
        if "penalties" in payload:
            score += " · " + tr(
                "Пенальти: {home}:{away}",
                home=payload["penalties"][0],
                away=payload["penalties"][1],
            )
        commentary = tr("\n\nПоследние события:\n") + commentary_html(timeline) if timeline else ""
        stats = (
            (
                tr(
                    "\nУдары: {p0} / {p1} · В створ: {p2} / {p3}",
                    p0=payload["shots"][0],
                    p1=payload["shots"][1],
                    p2=payload["on_target"][0],
                    p3=payload["on_target"][1],
                )
            )
            if "shots" in payload and "on_target" in payload
            else ""
        )
        if kind == "match_result":
            if payload["phase"] == "abandoned":
                return tr("Матч остановлен: {p0}. Итоговый результат не присуждён.", p0=score)
            if payload.get("player_of_match_name"):
                stats += "\n" + tr(
                    "⭐ Игрок матча: {player}",
                    player=escape(latin_name(payload["player_of_match_name"])),
                )
            return tr(
                "🏁 Финальный свисток: {p0}.{p1}{p2}\n\n/match — итог и отчёт о матче",
                p0=score,
                p1=stats,
                p2=commentary,
            )
        minute = payload.get("game_minute", payload["tick"] * 3)
        suffix = (
            tr(" · Перерыв")
            if payload["phase"] in {"half_time", "extra_break", "extra_half_time"}
            else tr(" · Дополнительное время")
            if payload["phase"].startswith("extra_")
            else ""
        )
        return tr(
            "⚽ {p0}′{p1}\n{p2}{p3}{p4}\n\n/match — управление матчем",
            p0=minute,
            p1=suffix,
            p2=score,
            p3=stats,
            p4=commentary,
        )
    if kind == "fixture_reminder":
        return tr("Скоро начинается матч вашего клуба. Проверьте состав перед игрой.")
    return tr("Ваш клуб ждёт вас. Откройте /start, чтобы продолжить управление.")


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
    if payload["kind"] == "payment_notice":
        from goaletto.infrastructure.models import PaySupportRequest, StarPayment

        paid = await session.get(StarPayment, _payload_uuid(payload, "payment_id"))
        if paid is None:
            return Decision.SKIP_STALE
        if message.chat_id == paid.payer_id:
            return Decision.DELIVER
        request = (
            await session.get(PaySupportRequest, _payload_uuid(payload, "request_id"))
            if payload.get("request_id")
            else None
        )
        return (
            Decision.DELIVER
            if request is not None
            and request.payment_id == paid.id
            and request.admin_chat_id == message.chat_id
            else Decision.SKIP_STALE
        )
    if payload["kind"] == "bankruptcy":
        from goaletto.infrastructure.models import BankruptcyReceipt

        receipt = await session.get(BankruptcyReceipt, _payload_uuid(payload, "receipt_id"))
        owner = await session.get(OwnershipPeriod, _payload_uuid(payload, "ownership_id"))
        manager = await session.get(Manager, owner.manager_id) if owner else None
        if (
            receipt is None
            or owner is None
            or manager is None
            or receipt.ownership_id != owner.id
            or owner.end_reason != "bankrupt"
            or manager.telegram_id != message.chat_id
            or payload["balance"] != receipt.balance
            or payload["assets"] != receipt.assets
        ):
            return Decision.SKIP_STALE
        return Decision.DELIVER if manager.bot_available else Decision.SKIP_UNAVAILABLE
    if payload["kind"] == "feedback_prompt":
        from goaletto.application.feedback_automation import deliverable
        from goaletto.infrastructure.models import FeedbackResponse

        response = await session.get(
            FeedbackResponse, _payload_uuid(payload, "response_id"), populate_existing=True
        )
        manager = await session.get(
            Manager, _payload_uuid(payload, "manager_id"), populate_existing=True
        )
        if (
            response is None
            or manager is None
            or response.manager_id != manager.id
            or manager.telegram_id != message.chat_id
        ):
            return Decision.SKIP_STALE
        if not manager.bot_available:
            return Decision.SKIP_UNAVAILABLE
        expected_target = (
            "calendar" if response.fixture_id else "lineup" if response.ownership_id else "claim"
        )
        if payload["trigger"] != response.trigger_kind or payload["target"] != expected_target:
            return Decision.SKIP_STALE
        return (
            Decision.DELIVER
            if await deliverable(session, response, manager, now)
            else Decision.SKIP_STALE
        )
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
    if kind == "friendly_invitation":
        from goaletto.infrastructure.models import FriendlyInvitation

        row = await session.get(
            FriendlyInvitation, _payload_uuid(payload, "invitation_id"), populate_existing=True
        )
        if (
            row is None
            or row.status != "pending"
            or row.challenged_ownership_id != ownership.id
            or row.away_club_id != club.id
            or _payload_uuid(payload, "club_id") != club.id
            or row.assigned_day.isoformat() != payload["day"]
            or row.assigned_day <= now.date()
        ):
            return Decision.SKIP_STALE
        challenger = await session.get(
            OwnershipPeriod, row.challenger_ownership_id, populate_existing=True
        )
        if (
            challenger is None
            or challenger.ended_at is not None
            or challenger.expires_at <= now
            or challenger.club_id != row.home_club_id
        ):
            return Decision.SKIP_STALE
        return Decision.DELIVER
    if kind == "training_cancelled":
        from goaletto.infrastructure.models import TrainingPlan

        plan = await session.get(
            TrainingPlan, _payload_uuid(payload, "plan_id"), populate_existing=True
        )
        return (
            Decision.DELIVER
            if plan
            and plan.ownership_id == ownership.id
            and plan.club_id == club.id
            and plan.week_start.date().isoformat() == payload["day"]
            and _payload_uuid(payload, "club_id") == club.id
            and plan.status == "cancelled"
            and plan.results
            and plan.results[0].get("reason") == "match_day"
            else Decision.SKIP_STALE
        )
    if kind == "staff_career":
        from goaletto.infrastructure.models import StaffCareer

        career = await session.get(
            StaffCareer, _payload_uuid(payload, "person_id"), populate_existing=True
        )
        return (
            Decision.DELIVER
            if career
            and career.status == "pending"
            and career.origin_club_id == club.id
            and now < career.expires_at
            and _payload_uuid(payload, "club_id") == club.id
            else Decision.SKIP_STALE
        )
    if kind == "scout_report":
        from goaletto.infrastructure.models import ScoutMission

        mission = await session.get(ScoutMission, _payload_uuid(payload, "mission_id"))
        return (
            Decision.DELIVER
            if mission
            and mission.ownership_id == ownership.id
            and mission.club_id == club.id
            and mission.status == "completed"
            and mission.expires_at
            and now < mission.expires_at
            else Decision.SKIP_STALE
        )
    if kind in {"player_lifecycle", "staff_lifecycle"}:
        from goaletto.infrastructure.models import PersonSeasonEvent, Player, Staff

        player = await session.get(
            Player if kind == "player_lifecycle" else Staff,
            _payload_uuid(payload, "person_id"),
            populate_existing=True,
        )
        player = cast(Player | Staff | None, player)
        prefix = "player" if kind == "player_lifecycle" else "staff"
        stage = prefix + ("_last_decision" if payload["action"] == "last_season" else "_retired")
        event = await session.scalar(
            select(PersonSeasonEvent).where(
                PersonSeasonEvent.person_id == _payload_uuid(payload, "person_id"),
                PersonSeasonEvent.season_id == _payload_uuid(payload, "season_id"),
                PersonSeasonEvent.stage == stage,
            )
        )
        if _payload_uuid(payload, "club_id") != club.id or player is None or event is None:
            return Decision.SKIP_STALE
        if payload["action"] == "last_season" and (
            player.club_id != club.id
            or player.status != "last_season"
            or player.last_season_id != event.season_id
            or not event.is_last_season
        ):
            return Decision.SKIP_STALE
        if payload["action"] == "retired" and (
            player.status != "retired" or player.retired_after_season_id != event.season_id
        ):
            return Decision.SKIP_STALE
        return Decision.DELIVER
    if kind == "market_notice":
        from goaletto.infrastructure.models import Auction, AuctionBid, TransferReceipt

        auction = await session.get(
            Auction, _payload_uuid(payload, "auction_id"), populate_existing=True
        )
        if (
            auction is None
            or _payload_uuid(payload, "club_id") != club.id
            or auction.person_id != _payload_uuid(payload, "person_id")
        ):
            return Decision.SKIP_STALE
        bid = (
            await session.get(AuctionBid, _payload_uuid(payload, "bid_id"))
            if payload.get("bid_id")
            else None
        )
        if payload.get("bid_id") and (
            bid is None or bid.auction_id != auction.id or bid.amount != payload["amount"]
        ):
            return Decision.SKIP_STALE
        seller = auction.seller_club_id == club.id and auction.seller_ownership_id == ownership.id
        buyer = (
            bid is not None
            and bid.buyer_club_id == club.id
            and bid.buyer_ownership_id == ownership.id
        )
        event = payload["event"]
        if event == "bid" and not (
            seller
            and auction.status == "open"
            and auction.highest_bid_id == (bid.id if bid else None)
        ):
            return Decision.SKIP_STALE
        if event == "outbid" and not (
            buyer
            and auction.status == "open"
            and auction.highest_bid_id != (bid.id if bid else None)
        ):
            return Decision.SKIP_STALE
        if event == "outbid" and auction.highest_bid_id:
            current = await session.get(AuctionBid, auction.highest_bid_id)
            if current is None or current.buyer_club_id == club.id:
                return Decision.SKIP_STALE
        if event in {"sold", "bought"}:
            receipt = await session.scalar(
                select(TransferReceipt).where(TransferReceipt.auction_id == auction.id)
            )
            if (
                auction.status != "sold"
                or receipt is None
                or receipt.price != payload["amount"]
                or not (seller if event == "sold" else buyer)
            ):
                return Decision.SKIP_STALE
        if event == "cancelled" and not (
            auction.status == "cancelled"
            and auction.close_reason == payload.get("reason")
            and (seller or buyer)
        ):
            return Decision.SKIP_STALE
        if event == "expired" and not (auction.status == "expired" and seller):
            return Decision.SKIP_STALE
        return Decision.DELIVER
    if kind == "ownership_reminder":
        if (
            _payload_uuid(payload, "club_id") != ownership.club_id
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
        if kind == "match_recovery":
            match = await session.get(
                Match, _payload_uuid(payload, "match_id"), populate_existing=True
            )
            deadline = datetime.fromisoformat(payload["resume_deadline"])
            if (
                fixture.status != "live"
                or now >= deadline
                or match is None
                or match.fixture_id != fixture.id
                or match.completed_tick != payload["tick"]
                or match.next_due_at != deadline
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
        if row.payload.get("kind") == "feedback_prompt" and await session.scalar(
            select(OutboxDeliveryAttempt.outbox_id)
            .where(
                OutboxDeliveryAttempt.outbox_id == row.id,
                OutboxDeliveryAttempt.outcome == "unknown",
            )
            .limit(1)
        ):
            row.status, row.last_error = "failed", "unknown_outcome"
            _clear_lease(row)
            continue
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
        if key is None:
            row.live_board_key = None  # Also normalize pending legacy final payloads.
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
                available_at=row.due_at,
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
    if message.payload["kind"] == "feedback_prompt" and outcome in {
        Outcome.SENT,
        Outcome.EDITED,
        Outcome.DUPLICATE,
    }:
        from goaletto.application.feedback import record_prompt_delivered

        response_id = _payload_uuid(message.payload, "response_id")
        assert response_id is not None
        await record_prompt_delivered(
            session, response_id=response_id, outbox_id=message.id, at=now
        )
        return
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
        if row.attempts >= MAX_ATTEMPTS or (
            row.payload.get("kind") == "feedback_prompt"
            and outcome in {Outcome.UNKNOWN, Outcome.TRANSIENT}
        ):
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
    session: AsyncSession,
    message_id: UUID,
    now: datetime,
    *,
    lease_owner: str,
    attempt: int,
    start_network: bool = True,
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
    if start_network:
        delivery_attempt.network_started_at = now
        delivery_attempt.outcome = "sending"
    return row, board.telegram_message_id if board else None


async def deliver_claimed(
    message: OutboxMessage,
    transport: TelegramTransport,
    now: datetime,
    *,
    existing_board_message_id: int | None = None,
    timezone_name: str = "UTC",
    prepared_text: str | None = None,
) -> tuple[Outcome, int | None, float | None]:
    """Call after preflight commit, without an open database transaction."""
    require_utc(now)
    try:
        text = (
            prepared_text
            if prepared_text is not None
            else render_text(message.payload, timezone_name=timezone_name)
        )
        if message.payload["kind"] == "match_board" and existing_board_message_id is not None:
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
