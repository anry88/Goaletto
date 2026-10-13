"""Manual voluntary-feedback router. Commits state before any Telegram I/O.

Network runs outside database transactions; prompt delivery is recorded in a
separate idempotent transaction only after transport confirmation. Comment
collection never absorbs commands or ordinary messages outside its window.
"""

from datetime import UTC, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, Update
from sqlalchemy import select

from goaletto.application.club_view import actor_for_callback
from goaletto.application.feedback import (
    COMMENT_MAX_CHARS,
    MANUAL_CAMPAIGN_SLUG,
    CommentExpired,
    CooldownActive,
    FeedbackDisabled,
    FeedbackSettings,
    cleanup_comments,  # noqa: F401  (re-exported for operator tooling)
    find_open_comment,
    opt_out,
    prompt_outbox_id,
    record_prompt_delivered,
    save_comment,
    skip_comment,
)
from goaletto.application.feedback import (
    answer_with_reason as _answer,
)
from goaletto.application.feedback import (
    open_prompt as _open,
)
from goaletto.application.feedback import (
    skip_prompt as _skip,
)
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.bot import feedback_views
from goaletto.bot.responses import edit_response
from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import FeedbackComment, FeedbackResponse, Manager
from goaletto.presentation.i18n import tr

router = Router(name="voluntary-feedback")


def feedback_settings_from_config(config: Settings | None = None) -> FeedbackSettings:
    config = config or Settings()
    return FeedbackSettings(
        enabled=bool(config.feedback_enabled),
        comment_retention_days=config.feedback_comment_retention_days,
        cooldown_days=int(config.feedback_cooldown_days),
    )


async def _mark_bot_unavailable(database: Database, telegram_id: int) -> None:
    try:
        async with database.transaction() as session:
            manager = await session.scalar(
                select(Manager).where(Manager.telegram_id == telegram_id)
            )
            if manager is not None:
                manager.bot_available = False
    except Exception:
        return


async def _render_for_status(
    database: Database,
    response_id: UUID,
    status: str,
    at: datetime,
    manager_id: UUID,
) -> tuple[str, InlineKeyboardMarkup]:
    if status == "prompted":
        return feedback_views.prompt_view(response_id)
    if status == "answered":
        async with database.transaction() as session:
            comment = await session.get(FeedbackComment, response_id)
            current = await session.get(FeedbackResponse, response_id)
        if comment is not None:
            return feedback_views.thanks_view()
        if (
            current is not None
            and current.comment_due_at is not None
            and at <= current.comment_due_at
        ):
            return feedback_views.comment_view(response_id)
        return feedback_views.thanks_view()
    if status == "skipped":
        return feedback_views.skipped_view()
    if status == "opted_out":
        return feedback_views.opted_out_view()
    return feedback_views.unavailable_view()


@router.message(Command("feedback"))
async def feedback_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None or message.chat.type != "private":
        return
    at = datetime.now(UTC)
    settings = feedback_settings_from_config()
    try:
        async with database.transaction() as session:
            actor = await actor_for(session, message.from_user.id, event_update.update_id, at)
            outcome = await _open(
                session, actor, slug=MANUAL_CAMPAIGN_SLUG, settings=settings, at=at
            )
        text, keyboard = await _render_for_status(
            database, outcome.response_id, outcome.status, at, actor.manager_id
        )
    except CooldownActive:
        text, keyboard = feedback_views.cooldown_view()
        await message.answer(text, reply_markup=keyboard)
        return
    except (PermissionDenied, FeedbackDisabled, ValueError):
        text, keyboard = feedback_views.unavailable_view()
        await message.answer(text, reply_markup=keyboard)
        return
    try:
        await message.answer(text, reply_markup=keyboard)
    except (TelegramForbiddenError, TelegramNotFound):
        await _mark_bot_unavailable(database, message.from_user.id)
        return
    except Exception:
        return
    # Delivery is confirmed only here; accepted/sent/unknown are never mixed.
    try:
        async with database.transaction() as session:
            await record_prompt_delivered(
                session,
                response_id=outcome.response_id,
                outbox_id=prompt_outbox_id(outcome.response_id),
                at=datetime.now(UTC),
            )
    except Exception:
        return


@router.callback_query(F.data.startswith("fb:"))
async def feedback_callback(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    at = datetime.now(UTC)
    settings = feedback_settings_from_config()
    try:
        action, response_id, arg = feedback_views.parse_callback(query.data or "")
    except ValueError:
        text, keyboard = feedback_views.unavailable_view()
        await edit_response(message, text, keyboard)
        return
    try:
        if action == "open":
            async with database.transaction() as session:
                actor = await actor_for_callback(
                    session, telegram_id=user.id, callback_id=query.id, at=at
                )
                outcome = await _open(
                    session, actor, slug=MANUAL_CAMPAIGN_SLUG, settings=settings, at=at
                )
            text, keyboard = await _render_for_status(
                database, outcome.response_id, outcome.status, at, actor.manager_id
            )
            try:
                await message.answer(text, reply_markup=keyboard)
            except (TelegramForbiddenError, TelegramNotFound):
                await _mark_bot_unavailable(database, user.id)
                return
            except Exception:
                return
            try:
                async with database.transaction() as session:
                    await record_prompt_delivered(
                        session,
                        response_id=outcome.response_id,
                        outbox_id=prompt_outbox_id(outcome.response_id),
                        at=datetime.now(UTC),
                    )
            except Exception:
                return
            return
        if response_id is None:
            raise ValueError("Stale feedback button")
        if action == "r":
            assert arg is not None
            from goaletto.application.feedback import REASON_CODES

            async with database.transaction() as session:
                actor = await actor_for_callback(
                    session, telegram_id=user.id, callback_id=query.id, at=at
                )
                result = await _answer(
                    session, actor, response_id=response_id, reason_code=REASON_CODES[arg], at=at
                )
            if result.status == "answered":
                text, keyboard = feedback_views.comment_view(response_id)
                # A repeated answer that already has text or a closed window is terminal.
                async with database.transaction() as session:
                    comment = await session.get(FeedbackComment, response_id)
                    current = await session.get(FeedbackResponse, response_id)
                if comment is not None:
                    text, keyboard = feedback_views.thanks_view()
                elif (
                    current is None or current.comment_due_at is None or at > current.comment_due_at
                ):
                    text, keyboard = feedback_views.expired_view()
            elif result.status == "skipped":
                text, keyboard = feedback_views.skipped_view()
            elif result.status == "opted_out":
                text, keyboard = feedback_views.opted_out_view()
            else:
                text, keyboard = feedback_views.unavailable_view()
        elif action == "s":
            async with database.transaction() as session:
                actor = await actor_for_callback(
                    session, telegram_id=user.id, callback_id=query.id, at=at
                )
                result = await _skip(session, actor, response_id=response_id, at=at)
            if result.status == "skipped":
                text, keyboard = feedback_views.skipped_view()
            elif result.status == "answered":
                text, keyboard = feedback_views.thanks_view()
            elif result.status == "opted_out":
                text, keyboard = feedback_views.opted_out_view()
            else:
                text, keyboard = feedback_views.unavailable_view()
        elif action == "o":
            async with database.transaction() as session:
                actor = await actor_for_callback(
                    session, telegram_id=user.id, callback_id=query.id, at=at
                )
                await opt_out(session, actor, response_id=response_id, at=at)
            text, keyboard = feedback_views.opted_out_view()
        elif action == "k":
            async with database.transaction() as session:
                actor = await actor_for_callback(
                    session, telegram_id=user.id, callback_id=query.id, at=at
                )
                await skip_comment(session, actor, response_id=response_id, at=at)
            text, keyboard = feedback_views.thanks_view()
        else:
            raise ValueError("Stale feedback button")
    except CooldownActive:
        text, keyboard = feedback_views.cooldown_view()
        await edit_response(message, text, keyboard)
        return
    except (PermissionDenied, FeedbackDisabled, ValueError):
        text, keyboard = feedback_views.unavailable_view()
        await edit_response(message, text, keyboard)
        return
    await edit_response(message, text, keyboard)


@router.message(F.text & ~F.text.startswith("/"))
async def feedback_comment_message(
    message: Message, database: Database, event_update: Update
) -> None:
    if message.from_user is None or message.chat.type != "private":
        return
    text = message.text
    if not isinstance(text, str) or not text.strip() or text.startswith("/"):
        raise SkipHandler()
    settings = feedback_settings_from_config()
    if not settings.enabled:
        raise SkipHandler()
    at = datetime.now(UTC)
    try:
        async with database.transaction() as session:
            actor = await actor_for(session, message.from_user.id, event_update.update_id, at)
            target = await find_open_comment(session, actor.manager_id, at)
            if target is None:
                raise SkipHandler()
            response_id = target.id
            try:
                await save_comment(session, actor, response_id=response_id, text=text, at=at)
            except ValueError as error:
                if isinstance(error, CommentExpired):
                    raise
                # Length/validation failures never echo the free text.
                raise ValueError("Invalid comment") from None
        text, keyboard = feedback_views.thanks_view()
        await message.answer(text, reply_markup=keyboard)
    except CommentExpired:
        text, keyboard = feedback_views.expired_view()
        await message.answer(text, reply_markup=keyboard)
        return
    except PermissionDenied:
        raise SkipHandler() from None
    except ValueError as error:
        message_text = str(error)
        if message_text == "Invalid comment" and len(text.strip()) > COMMENT_MAX_CHARS:
            await message.answer(tr("Комментарий слишком длинный: максимум 500 символов."))
            return
        return


__all__ = ["router", "feedback_settings_from_config"]
