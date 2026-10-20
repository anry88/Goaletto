"""Bounded update/API labels. Message text, IDs and callback arguments never escape."""

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import Response
from aiogram.methods.base import TelegramMethod, TelegramType
from aiogram.types import CallbackQuery, Message, TelegramObject

from goaletto.bot.commands import COMMANDS
from goaletto.infrastructure.metrics import METRICS, Metrics

COMMAND_NAMES = frozenset(c[0] for c in COMMANDS)
CALLBACK_PREFIXES = frozenset(
    (
        "vc",
        "ln",
        "lv",
        "hx",
        "table",
        "report",
        "nav",
        "ntf",
        "tz",
        "lang",
        "fi",
        "st",
        "ac",
        "tr",
        "ca",
        "fr",
        "mk",
        "fa",
        "sf",
        "sc",
        "fb",
        "entry",
        "claim",
    )
)
API_METHODS = frozenset(
    (
        "getUpdates",
        "getMe",
        "getWebhookInfo",
        "sendMessage",
        "editMessageText",
        "editMessageReplyMarkup",
        "sendPhoto",
        "editMessageMedia",
        "answerCallbackQuery",
        "setMyCommands",
        "setChatMenuButton",
        "getFile",
        "deleteMessage",
    )
)


def route(event: TelegramObject) -> tuple[str, str]:
    if isinstance(event, CallbackQuery):
        prefix = (event.data or "").split(":", 1)[0]
        return "callback", "button_" + prefix if prefix in CALLBACK_PREFIXES else "unknown"
    if isinstance(event, Message):
        first = (event.text or "").split(maxsplit=1)
        if first and first[0].startswith("/"):
            name = first[0][1:].split("@", 1)[0].lower()
            return "command", name if name in COMMAND_NAMES else "unknown"
        return "message", "text_or_media"
    return "other", "unknown"


class UpdateMetrics(BaseMiddleware):
    def __init__(self, metrics: Metrics = METRICS) -> None:
        self.metrics = metrics
        for name in sorted(COMMAND_NAMES):
            self.metrics.starts.labels("command", name)
        for kind in ("command", "callback", "message", "other"):
            self.metrics.inflight.labels(kind)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        kind, name = route(event)
        started, outcome = perf_counter(), "error"
        self.metrics.starts.labels(kind, name).inc()
        self.metrics.inflight.labels(kind).inc()
        try:
            result = await handler(event, data)
            outcome = (
                "denied"
                if data.get("metrics_access_denied")
                else "unhandled"
                if result is UNHANDLED
                else "handled"
            )
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            self.metrics.updates.labels(kind, name, outcome).inc()
            self.metrics.update_seconds.labels(kind, name).observe(perf_counter() - started)
            self.metrics.inflight.labels(kind).dec()


class TelegramMetrics(BaseRequestMiddleware):
    def __init__(self, metrics: Metrics = METRICS) -> None:
        self.metrics = metrics

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        name = method.__api_method__ if method.__api_method__ in API_METHODS else "other"
        started, outcome = perf_counter(), "error"
        try:
            result = await make_request(bot, method)
            outcome = "accepted"
            return result
        except TelegramRetryAfter as error:
            outcome = "rate_limited"
            self.metrics.retry_seconds.labels(name).observe(error.retry_after)
            raise
        except TelegramForbiddenError:
            outcome = "forbidden"
            raise
        except TelegramBadRequest:
            outcome = "bad_request"
            raise
        except TelegramNetworkError:
            outcome = "network_unknown"
            raise
        except TimeoutError:
            outcome = "timeout_unknown"
            raise
        except asyncio.CancelledError:
            outcome = "cancelled_unknown"
            raise
        finally:
            self.metrics.api.labels(name, outcome).inc()
            self.metrics.api_seconds.labels(name).observe(perf_counter() - started)
