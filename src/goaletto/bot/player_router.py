"""Player card router. Handles vc:p:<person>:<page>. Text card always works.

Portrait photo is best-effort: the same face is re-rendered locally through
portraits/whole.py with garment masks, cached by the full rendering inputs.
A missing illustrated variant shows an explicit text notice and never
substitutes another person's face.
"""

from datetime import UTC, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_view import (
    PlayerCard,
    actor_for_callback,
    get_player_card,
)
from goaletto.application.game_actions import PermissionDenied
from goaletto.bot.club_cards import parse_view_callback, player_view, stale_view
from goaletto.bot.portrait_cache import (
    InMemoryPortraitFileCache,
    PortraitFileStore,
    portrait_cache_key,
)
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.portrait_files import load_file_id, store_file_id
from goaletto.portraits.whole import (
    WholePortraitRecipe,
    recipe_for_age,
    render_whole,
)
from goaletto.presentation.i18n import tr

router = Router(name="player-view")

_store: PortraitFileStore = InMemoryPortraitFileCache()


def set_portrait_store(store: PortraitFileStore) -> None:
    """Codex wires the durable Postgres store here at startup; tests inject fakes."""
    global _store
    _store = store


def portrait_store() -> PortraitFileStore:
    return _store


def resolve_portrait_variant(
    *, catalog_version: int, identity_id: str, variant_id: str, age: int
) -> WholePortraitRecipe | None:
    """Return the age variant of the SAME face, or None when not illustrated."""
    try:
        base = WholePortraitRecipe(catalog_version, identity_id, variant_id)
        return recipe_for_age(base, bank="players", age=age)
    except ValueError:
        return None


def render_portrait_png(
    recipe: WholePortraitRecipe, *, kit_primary: str, kit_secondary: str, size: int = 256
) -> bytes:
    from io import BytesIO

    image = render_whole(recipe, kit_primary=kit_primary, kit_secondary=kit_secondary, size=size)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def build_player_response(
    session: AsyncSession,
    telegram_id: int,
    callback_id: str,
    at: datetime,
    person_id: UUID,
    return_page: int,
) -> tuple[str, InlineKeyboardMarkup, PlayerCard, WholePortraitRecipe | None, str]:
    actor = await actor_for_callback(
        session, telegram_id=telegram_id, callback_id=callback_id, at=at
    )
    card = await get_player_card(session, actor, person_id)
    recipe = resolve_portrait_variant(
        catalog_version=card.catalog_version,
        identity_id=card.appearance_identity_id,
        variant_id=card.appearance_variant_id,
        age=card.age,
    )
    text, keyboard = player_view(card, return_page, portrait_ready=recipe is not None)
    key = ""
    if recipe is not None:
        key = portrait_cache_key(
            identity_id=recipe.identity_id,
            variant_id=recipe.variant_id,
            catalog_version=recipe.catalog_version,
            kit_primary=card.kit_primary,
            kit_secondary=card.kit_secondary,
            kit_version=card.kit_version,
            size=256,
            background="transparent",
        )
    return text, keyboard, card, recipe, key


@router.callback_query(F.data.startswith("vc:p:"))
async def player_card(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    message = query.message
    user = query.from_user
    if not isinstance(message, Message) or user is None:
        return
    try:
        kind, person_id, page = parse_view_callback(query.data or "")
        if kind != "player" or person_id is None:
            raise ValueError("Not a player callback")
        now = datetime.now(UTC)
        async with database.transaction() as session:
            text, keyboard, card, recipe, key = await build_player_response(
                session, user.id, query.id, now, person_id, page
            )
        if recipe is not None and key and caption_fits(text):
            shown = await _maybe_edit_portrait(
                query,
                recipe,
                key,
                text=text,
                keyboard=keyboard,
                database=database,
                kit_version=card.kit_version,
                kit_primary=card.kit_primary,
                kit_secondary=card.kit_secondary,
            )
            if shown:
                return
        await edit_response(message, text, keyboard)
    except PermissionDenied:
        await edit_response(
            message, tr("Нет доступа: клуб больше не принадлежит вам. Откройте /start.")
        )
    except ValueError:
        text, keyboard = stale_view()
        await edit_response(message, text, keyboard)


async def _maybe_edit_portrait(
    query: CallbackQuery,
    recipe: WholePortraitRecipe,
    key: str,
    *,
    text: str,
    keyboard: InlineKeyboardMarkup,
    kit_primary: str,
    kit_secondary: str,
    kit_version: int = 1,
    database: Database | None = None,
) -> bool:
    """Replace the same message, with caption/buttons; network outside transactions."""
    message = query.message
    if not isinstance(message, Message):
        return False
    try:
        if database is not None:
            async with database.transaction() as session:
                file_id = await load_file_id(session, key)
        else:
            file_id = await _store.get(key)
        if file_id is not None:
            try:
                await message.edit_media(
                    InputMediaPhoto(media=file_id, caption=text, parse_mode="HTML"),
                    reply_markup=keyboard,
                )
                return True
            except TelegramBadRequest as error:
                if "message is not modified" in error.message.lower():
                    return True
                if not any(
                    code in error.message.lower()
                    for code in ("wrong file", "file_id", "file identifier")
                ):
                    raise
        png = render_portrait_png(
            recipe, kit_primary=kit_primary, kit_secondary=kit_secondary, size=256
        )
        sent = await message.edit_media(
            InputMediaPhoto(
                media=BufferedInputFile(png, filename="p.png"), caption=text, parse_mode="HTML"
            ),
            reply_markup=keyboard,
        )
        photos = getattr(sent, "photo", None) or []
        if photos:
            if database is not None:
                async with database.transaction() as session:
                    await store_file_id(
                        session,
                        cache_key=key,
                        file_id=photos[-1].file_id,
                        identity_id=recipe.identity_id,
                        variant_id=recipe.variant_id,
                        catalog_version=recipe.catalog_version,
                        kit_primary=kit_primary,
                        kit_secondary=kit_secondary,
                        kit_version=kit_version,
                        size=256,
                        background="transparent",
                        at=datetime.now(UTC),
                    )
            else:
                await _store.put(key, photos[-1].file_id)
        return True
    except TelegramBadRequest as error:
        return "message is not modified" in error.message.lower()
    except (TelegramAPIError, OSError, ValueError):
        return False


def caption_fits(text: str) -> bool:
    """Telegram caption budget after parsing our escaped HTML, in UTF-16 units."""
    import re
    from html import unescape

    plain = unescape(re.sub(r"</?b>", "", text))
    return len(plain.encode("utf-16-le")) // 2 <= 1024
