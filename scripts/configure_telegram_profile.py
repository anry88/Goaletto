"""Apply Goaletto branding using the bot token from the ignored local .env."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import FSInputFile, InputProfilePhotoStatic

from goaletto.config import Settings

ROOT = Path(__file__).resolve().parents[1]


async def configure(apply_icon: bool) -> None:
    root = ROOT
    branding = root / "assets" / "brand"
    profile = json.loads((branding / "telegram-profile.json").read_text())
    token = Settings().bot_token.get_secret_value()
    if not token:
        raise SystemExit("Set GOALETTO_BOT_TOKEN in local .env")
    if len(profile["description"]) > 512 or len(profile["short_description"]) > 120:
        raise SystemExit("Telegram description exceeds its length limit")
    receipt: dict[str, Any] = {"applied_at": datetime.now(UTC).isoformat()}
    try:
        async with Bot(token) as bot:
            me = await bot.get_me()
            if me.id != 8941820906:
                raise RuntimeError("Unexpected bot identity")
            for language in ("", "en"):
                await bot.set_my_description(
                    description=profile["description"], language_code=language
                )
                await bot.set_my_short_description(
                    short_description=profile["short_description"], language_code=language
                )
                actual = await bot.get_my_description(language_code=language)
                actual_short = await bot.get_my_short_description(language_code=language)
                if actual.description != profile["description"]:
                    raise RuntimeError("Description verification failed")
                if actual_short.short_description != profile["short_description"]:
                    raise RuntimeError("Short description verification failed")
            receipt["bot_id"] = me.id
            receipt["username"] = me.username
            receipt["descriptions_verified"] = True
            if apply_icon:
                icon = branding / "goaletto-icon.jpg"
                if not icon.is_file():
                    raise RuntimeError("Generate the brand icon before applying it")
                applied = await bot.set_my_profile_photo(
                    photo=InputProfilePhotoStatic(photo=FSInputFile(icon))
                )
                if not applied:
                    raise RuntimeError("Profile photo update was not confirmed")
                photos = await bot.get_user_profile_photos(user_id=me.id, limit=1)
                if not photos.photos:
                    raise RuntimeError("Profile photo read-back returned no photo")
                work = root / "work"
                work.mkdir(exist_ok=True)
                await bot.download(
                    photos.photos[0][-1], destination=work / "telegram-icon-confirmed.jpg"
                )
                receipt["profile_photo_verified"] = True
                receipt["profile_photo_count"] = photos.total_count
            work = root / "work"
            work.mkdir(exist_ok=True)
            (work / "telegram-profile-receipt.json").write_text(
                json.dumps(receipt, indent=2) + "\n"
            )
            print(json.dumps(receipt))
    except Exception as error:
        message = str(error).replace(token, "<redacted>")
        print(f"Telegram profile update failed: {type(error).__name__}: {message}")
        raise SystemExit(1) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-icon", action="store_true")
    args = parser.parse_args()
    asyncio.run(configure(args.apply_icon))


if __name__ == "__main__":
    main()
