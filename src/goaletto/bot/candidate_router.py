"""Free-agent and staff market; commit hiring and replenishment before delivery."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.candidates import load_candidates, sign_candidate
from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.staff_cards import load_staff_card
from goaletto.application.staff_changes import HeadCoachRequired, execute_change, quote_change
from goaletto.bot.candidate_views import (
    candidate_list,
    candidate_quote,
    parse_candidate,
    staff_change_view,
)
from goaletto.bot.card_images import photo_response, staff_image
from goaletto.bot.responses import edit_response
from goaletto.bot.staff_cards import staff_card_view
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import CandidateOffer
from goaletto.presentation.i18n import tr

router = Router(name="candidates")


@router.message(Command("freeagents", "staff"))
async def candidates_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    kind = "staff" if (message.text or "").split()[0].split("@")[0] == "/staff" else "player"
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = candidate_list(await load_candidates(session, actor, kind), kind)
    except (PermissionDenied, ValueError):
        await message.answer(tr("Раздел недоступен. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("cg:"))
async def candidates_callback(
    query: CallbackQuery, database: Database, event_update: Update
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    image = None
    try:
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            action, person_id, owner_id, value = parse_candidate(query.data or "")
            if action == "v":
                if owner_id is None or person_id is None or not 0 <= value <= 999:
                    raise ValueError("Invalid staff card")
                card = await load_staff_card(session, actor, owner_id, person_id)
                text, keyboard = staff_card_view(card, value)
                image = staff_image(
                    card.person.appearance_identity_id,
                    card.person.appearance_variant_id,
                    card.catalog_version,
                    card.person.age,
                    card.kit_primary,
                    card.kit_secondary,
                    card.kit_version,
                )
            elif action in {"p", "s"}:
                kind = "player" if action == "p" else "staff"
                text, keyboard = candidate_list(
                    await load_candidates(session, actor, kind), kind, value
                )
            elif action == "x":
                if person_id is None:
                    raise ValueError("Missing confirmation")
                await execute_change(session, actor, person_id, at=datetime.now(UTC))
                text, keyboard = candidate_list(
                    await load_candidates(
                        session, actor, "staff", record=False, refresh_supply=False
                    ),
                    "staff",
                    notice=tr("Штаб обновлён. Условия зарплаты сохранены."),
                )
            elif action == "d":
                if owner_id is None or person_id is None:
                    raise ValueError("Missing career offer")
                from goaletto.application.staff_career import decline_offer

                await decline_offer(session, actor, person_id, owner_id)
                text, keyboard = candidate_list(
                    await load_candidates(session, actor, "staff", record=False),
                    "staff",
                    notice=tr("Предложение отклонено. Сотрудник вышел на общий рынок."),
                )
            elif action == "f":
                if owner_id is None or person_id is None:
                    raise ValueError("Missing employee")
                quote = await quote_change(session, actor, owner_id, outgoing_id=person_id)
                text, keyboard = staff_change_view(quote)
            else:
                offer = await session.get(CandidateOffer, person_id)
                if offer is None or person_id is None or owner_id is None:
                    raise ValueError("Unknown candidate")
                kind = offer.kind
                if kind == "staff":
                    if action != "q":
                        raise ValueError("Staff requires a fresh confirmation")
                    quote = await quote_change(
                        session, actor, owner_id, incoming_id=person_id, quoted_fee=value
                    )
                    text, keyboard = staff_change_view(quote)
                elif action == "b":
                    receipt = await sign_candidate(
                        session, actor, owner_id, person_id, value, at=datetime.now(UTC)
                    )
                    text, keyboard = candidate_list(
                        await load_candidates(
                            session, actor, receipt.kind, record=False, refresh_supply=False
                        ),
                        receipt.kind,
                        notice=tr("Контракт подписан. Проверьте состав и расходы клуба."),
                    )
                else:
                    screen = await load_candidates(session, actor, kind)
                    item = next((i for i in screen.items if i.person.id == person_id), None)
                    if screen.ownership_id != owner_id or item is None or item.offer.fee != value:
                        raise ValueError("Old quote")
                    text, keyboard = candidate_quote(item, owner_id)
    except HeadCoachRequired:
        await edit_response(
            query.message,
            tr("Главного тренера можно только заменить через найм другого. Откройте /staff."),
        )
        return
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr(
                "Подписание недоступно: кандидат занят, кнопка устарела, роль уже занята "
                "или не хватает денег/места. Для игроков нужно открытое трансферное "
                "окно. Откройте /market или /staff."
            ),
        )
        return
    if image is not None and await photo_response(query.message, database, text, keyboard, image):
        return
    await edit_response(query.message, text, keyboard)
