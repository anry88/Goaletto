"""One photo, caption and keyboard; durable cache and network after commit."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

from goaletto.bot.player_router import caption_fits, render_portrait_png
from goaletto.bot.portrait_cache import portrait_cache_key
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.portrait_files import load_file_id, store_file_id
from goaletto.portraits.whole import WholePortraitRecipe, recipe_for_age, recipe_for_variant


@dataclass(frozen=True)
class CardImage:
    identity: str
    variant: str
    catalog_version: int
    primary: str
    secondary: str
    kit_version: int
    render: Callable[[], bytes]
    background: str = "transparent"

    @property
    def key(self) -> str:
        return portrait_cache_key(
            identity_id=self.identity,
            variant_id=self.variant,
            catalog_version=self.catalog_version,
            kit_primary=self.primary,
            kit_secondary=self.secondary,
            kit_version=self.kit_version,
            size=256,
            background=self.background,
        )


def staff_image(
    identity: str,
    variant: str,
    catalog_version: int,
    age: int,
    primary: str,
    secondary: str,
    kit_version: int,
) -> CardImage | None:
    try:
        base = recipe_for_variant(variant)
        if base != WholePortraitRecipe(catalog_version, identity, variant):
            return None
        try:
            recipe = recipe_for_age(base, bank="staff", age=age)
        except ValueError:
            recipe = base  # Preserve this person's known face when the staff band is missing.
        return CardImage(
            recipe.identity_id,
            recipe.variant_id,
            recipe.catalog_version,
            primary,
            secondary,
            kit_version,
            lambda: render_portrait_png(recipe, kit_primary=primary, kit_secondary=secondary),
        )
    except (ValueError, OSError):
        return None


async def photo_response(
    message: Message,
    database: Database,
    text: str,
    keyboard: InlineKeyboardMarkup,
    image: CardImage,
    *,
    edit: bool = True,
) -> bool:
    if not caption_fits(text):
        return False
    try:
        async with database.transaction() as session:
            file_id = await load_file_id(session, image.key)
        for cached in (True, False) if file_id else (False,):
            if cached:
                assert file_id is not None
                media: str | BufferedInputFile = file_id
            else:
                media = BufferedInputFile(image.render(), filename="card.png")
            try:
                if edit:
                    sent = await message.edit_media(
                        InputMediaPhoto(media=media, caption=text, parse_mode="HTML"),
                        reply_markup=keyboard,
                    )
                else:
                    sent = await message.answer_photo(
                        photo=media, caption=text, parse_mode="HTML", reply_markup=keyboard
                    )
            except TelegramBadRequest as error:
                reason = error.message.lower()
                if "message is not modified" in reason:
                    return True
                if cached and any(
                    w in reason for w in ("wrong file", "file_id", "file identifier")
                ):
                    continue
                raise
            if not cached and isinstance(sent, Message) and sent.photo:
                async with database.transaction() as session:
                    await store_file_id(
                        session,
                        cache_key=image.key,
                        identity_id=image.identity,
                        variant_id=image.variant,
                        catalog_version=image.catalog_version,
                        kit_primary=image.primary,
                        kit_secondary=image.secondary,
                        kit_version=image.kit_version,
                        size=256,
                        background=image.background,
                        file_id=sent.photo[-1].file_id,
                        at=datetime.now(UTC),
                    )
            return True
    except (TelegramAPIError, OSError, ValueError):
        return False
    return False
