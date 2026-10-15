"""Training command and callback IO, after database commit."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update
from sqlalchemy import select

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for, owned_club
from goaletto.application.training import load_training, select_training
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import expand_id
from goaletto.bot.training_views import parse_choice, training_report, training_view
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import TrainingPlan
from goaletto.presentation.i18n import tr

router = Router(name="training")


@router.message(Command("train"))
async def train_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = training_view(await load_training(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Тренировки недоступны. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("tr:"))
async def training_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            data = query.data or ""
            if data.startswith("tr:r:"):
                parts = data.split(":")
                if (
                    len(parts) != 4
                    or not parts[3].isascii()
                    or not parts[3].isdigit()
                    or len(data.encode()) > 64
                ):
                    raise ValueError("Invalid training report")
                ownership, club = await owned_club(session, actor)
                plan = await session.scalar(
                    select(TrainingPlan).where(
                        TrainingPlan.id == expand_id(parts[2]),
                        TrainingPlan.club_id == club.id,
                        TrainingPlan.ownership_id == ownership.id,
                        TrainingPlan.status.in_(("applied", "cancelled")),
                    )
                )
                if plan is None:
                    raise PermissionDenied("Training report belongs to another owner")
                text, keyboard = training_report(plan, club.name, int(parts[3]))
            elif data == "tr:open":
                text, keyboard = training_view(await load_training(session, actor))
            else:
                action, ownership_id, week, focus, intensity, executor_id = parse_choice(data)
                screen = await load_training(session, actor, record=False)
                if ownership_id != screen.ownership_id or week != screen.week_start:
                    text, keyboard = training_view(
                        screen, notice=tr("Кнопка устарела. Выберите тренировку заново.")
                    )
                elif action == "s":
                    await select_training(
                        session,
                        actor,
                        ownership_id=ownership_id,
                        week=week,
                        focus=focus,
                        intensity=intensity,
                        executor_id=executor_id,
                    )
                    text, keyboard = training_view(
                        await load_training(session, actor, record=False),
                        notice=tr("Тренировка сохранена."),
                    )
                else:
                    if executor_id is not None and (
                        focus.value != "set_pieces"
                        or executor_id not in {m.id for m in screen.members}
                    ):
                        raise ValueError("Invalid executor")
                    text, keyboard = training_view(
                        screen, focus=focus, intensity=intensity, executor_id=executor_id
                    )
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr(
                "Тренировка не сохранена: проверьте дату, свободный день, доступного игрока "
                "и нужного специалиста в /staff. Откройте /train."
            ),
        )
        return
    await edit_response(query.message, text, keyboard)
