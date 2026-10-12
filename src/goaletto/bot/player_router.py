"""Player card router. Handles vc:p:<person>:<page>. Text card always works.

Portrait photo is best-effort: the same face is re-rendered locally through
portraits/whole.py with garment masks, cached by the full rendering inputs.
A missing illustrated variant shows an explicit text notice and never
substitutes another person's face.
"""

from datetime import UTC, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
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
from goaletto.portraits.whole import (
    WholePortraitRecipe,
    recipe_for_age,
    render_whole,
)

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
async def player_card(query: CallbackQuery, session: AsyncSession) -> None:
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
        text, keyboard, card, recipe, key = await build_player_response(
            session, user.id, query.id, now, person_id, page
        )
        await message.edit_text(text, reply_markup=keyboard)
        if recipe is not None and key:
            await _maybe_send_portrait(
                query,
                recipe,
                key,
                kit_primary=card.kit_primary,
                kit_secondary=card.kit_secondary,
            )
    except PermissionDenied:
        await message.edit_text("Нет доступа: клуб больше не принадлежит вам. Откройте /start.")
    except ValueError:
        text, keyboard = stale_view()
        await message.edit_text(text, reply_markup=keyboard)


async def _maybe_send_portrait(
    query: CallbackQuery,
    recipe: WholePortraitRecipe,
    key: str,
    *,
    kit_primary: str,
    kit_secondary: str,
) -> None:
    """Best-effort photo; any failure keeps the text card as the source of truth."""
    message = query.message
    if not isinstance(message, Message):
        return
    try:
        file_id = await _store.get(key)
        if file_id is not None:
            try:
                await message.answer_photo(photo=file_id)
                return
            except Exception:
                pass  # Stale file_id (e.g. after restart): re-render same face.
        png = render_portrait_png(
            recipe, kit_primary=kit_primary, kit_secondary=kit_secondary, size=256
        )
        sent = await message.answer_photo(photo=BufferedInputFile(png, filename="p.png"))
        photos = getattr(sent, "photo", None) or []
        if photos:
            await _store.put(key, photos[-1].file_id)
    except Exception:
        return
