"""Portrait cache key, store durability and fake-transport router tests."""

from typing import Any
from uuid import uuid4

import pytest
from aiogram.types import InlineKeyboardMarkup

from goaletto.bot import player_router, squad_router
from goaletto.bot.club_cards import player_callback, squad_callback
from goaletto.bot.player_router import resolve_portrait_variant
from goaletto.bot.portrait_cache import (
    InMemoryPortraitFileCache,
    portrait_cache_key,
    validate_file_id,
)

BASE = {
    "identity_id": "p0001",
    "variant_id": "p0001-player-adult",
    "catalog_version": 2,
    "kit_primary": "#743D42",
    "kit_secondary": "#F2E9D8",
    "kit_version": 1,
    "size": 256,
    "background": "transparent",
}


def test_cache_key_covers_every_rendering_input() -> None:
    first = portrait_cache_key(**BASE)  # type: ignore[arg-type]
    assert first == portrait_cache_key(**BASE)  # type: ignore[arg-type]
    assert len(first) == 64
    for field, other in (
        ("identity_id", "p0002"),
        ("variant_id", "p0001-player-youth"),
        ("catalog_version", 3),
        ("kit_primary", "#315B49"),
        ("kit_secondary", "#E2BF57"),
        ("kit_version", 2),
        ("size", 96),
        ("background", "white"),
    ):
        changed = dict(BASE)
        changed[field] = other
        assert portrait_cache_key(**changed) != first  # type: ignore[arg-type]
    # Kit colors are case-insensitive but still keyed.
    assert portrait_cache_key(**{**BASE, "kit_primary": "#743d42"}) == first  # type: ignore[arg-type]
    for bad in (
        {**BASE, "size": 512},
        {**BASE, "kit_primary": "red"},
        {**BASE, "kit_version": 0},
        {**BASE, "identity_id": ""},
        {**BASE, "background": ""},
    ):
        with pytest.raises(ValueError):
            portrait_cache_key(**bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        validate_file_id("")


async def test_store_survives_restart_and_rejects_bad_ids() -> None:
    durable: dict[str, str] = {}
    cache = InMemoryPortraitFileCache(durable)
    key = portrait_cache_key(**BASE)  # type: ignore[arg-type]
    assert await cache.get(key) is None
    await cache.put(key, "file-id-1")
    restarted = InMemoryPortraitFileCache(durable)
    assert await restarted.get(key) == "file-id-1"
    with pytest.raises(ValueError):
        await cache.put(key, "")


def test_missing_variant_never_borrows_another_face() -> None:
    assert (
        resolve_portrait_variant(
            catalog_version=2,
            identity_id="no-such-face",
            variant_id="no-such-variant",
            age=25,
        )
        is None
    )


def test_kit_change_preserves_face_and_alpha(monkeypatch: pytest.MonkeyPatch) -> None:
    from goaletto.portraits.compositor import asset_root
    from goaletto.portraits.whole import recipe_for_variant, render_whole

    monkeypatch.setattr("goaletto.portraits.whole.whole_root", lambda: asset_root() / "whole-v1")
    recipe = recipe_for_variant("p01-player24")
    first = render_whole(recipe, kit_primary="#743D42", kit_secondary="#F2E9D8")
    second = render_whole(recipe, kit_primary="#315B49", kit_secondary="#E2BF57")
    assert first.getchannel("A").tobytes() == second.getchannel("A").tobytes()
    assert first.crop((0, 0, 256, 128)).tobytes() == second.crop((0, 0, 256, 128)).tobytes()
    assert first.tobytes() != second.tobytes()


class FakeUser:
    def __init__(self, uid: int) -> None:
        self.id = uid


class FakePhoto:
    def __init__(self, file_id: str) -> None:
        self.file_id = file_id


class FakeSent:
    def __init__(self, file_id: str = "uploaded-id") -> None:
        self.photo = [FakePhoto(file_id)]


def make_message(fail_on: tuple[str, ...] = ()) -> Any:
    """Real aiogram Message (passes handler isinstance guards) with recorded IO."""

    from datetime import UTC, datetime

    from aiogram.types import Chat, Message

    message = Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"))
    edits: list[tuple[str, Any]] = []
    photos: list[Any] = []

    async def edit_text(text: str, reply_markup: Any = None) -> None:
        edits.append((text, reply_markup))

    async def edit_media(media: Any, **kwargs: Any) -> FakeSent:
        photo = media.media
        assert media.caption == "card"
        assert kwargs["reply_markup"] is not None
        if isinstance(photo, str) and photo in fail_on:
            from aiogram.exceptions import TelegramBadRequest
            from aiogram.methods import SendPhoto

            raise TelegramBadRequest(
                SendPhoto(chat_id=123, photo="uploaded-id"), "wrong file identifier"
            )
        photos.append(photo)
        return FakeSent()

    object.__setattr__(message, "edits", edits)
    object.__setattr__(message, "photos", photos)
    object.__setattr__(message, "edit_text", edit_text)
    object.__setattr__(message, "edit_media", edit_media)
    return message


def make_query(data: str, message: Any, qid: str = "q-1") -> Any:
    from aiogram.types import CallbackQuery, User

    query = CallbackQuery(
        id=qid,
        from_user=User(id=123, is_bot=False, first_name="T"),
        chat_instance="ci",
        data=data,
        message=message,
    )
    object.__setattr__(query, "calls", [])

    async def answer(*args: Any, **kwargs: Any) -> None:
        query.calls.append("answer")  # type: ignore[attr-defined]

    object.__setattr__(query, "answer", answer)
    return query


class TouchSession:
    """Minimal session: records whether DB was touched before answer."""

    def __init__(self, query: Any) -> None:
        self.query = query
        self.touched = False

    def _touch(self) -> None:
        self.touched = True
        assert self.query.calls == ["answer"], "answerCallbackQuery must come first"

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def transaction(self):
        self._touch()
        yield self

    def in_transaction(self) -> bool:
        self._touch()
        return True

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        self._touch()

    async def scalar(self, *args: Any, **kwargs: Any) -> None:
        self._touch()
        return None

    async def scalars(self, *args: Any, **kwargs: Any) -> None:
        self._touch()
        raise AssertionError("unreachable in answer-order test")


async def test_answer_comes_before_heavy_work_and_old_callbacks_safe() -> None:
    message = make_message()
    query = make_query(squad_callback(1), message)
    session = TouchSession(query)
    await squad_router.squad_page(query, session)  # type: ignore[arg-type]
    assert query.calls == ["answer"]
    # Old callbacks never reach our routers: codecs reject them explicitly.
    from goaletto.bot.club_cards import is_view_callback, parse_view_callback

    for old in ("preview:squad", "game:club", "table:a:b:1", "report:abc"):
        assert not is_view_callback(old)
        with pytest.raises(ValueError):
            parse_view_callback(old)


async def test_portrait_retry_uses_file_id_and_recovers_from_stale() -> None:
    from goaletto.portraits.whole import WholePortraitRecipe

    recipe = WholePortraitRecipe(2, "p0001", "p0001-player-adult")
    key = portrait_cache_key(**BASE)  # type: ignore[arg-type]
    store = InMemoryPortraitFileCache()
    player_router.set_portrait_store(store)
    try:
        rendered: list[tuple[str, str]] = []

        def fake_render(
            recipe: WholePortraitRecipe,
            *,
            kit_primary: str,
            kit_secondary: str,
            size: int = 256,
        ) -> bytes:
            rendered.append((kit_primary, kit_secondary))
            return b"png-bytes"

        original = player_router.render_portrait_png
        player_router.render_portrait_png = fake_render  # type: ignore[assignment]
        try:
            message = make_message()
            query = make_query(player_callback(uuid4()), message)
            await player_router._maybe_edit_portrait(
                query,
                recipe,
                key,
                text="card",
                keyboard=InlineKeyboardMarkup(inline_keyboard=[]),
                kit_primary="#743D42",
                kit_secondary="#F2E9D8",
            )
            assert rendered == [("#743D42", "#F2E9D8")]
            assert len(message.photos) == 1
            # Second view reuses the durable file_id without re-rendering.
            rendered.clear()
            query2 = make_query(player_callback(uuid4()), message)
            await player_router._maybe_edit_portrait(
                query2,
                recipe,
                key,
                text="card",
                keyboard=InlineKeyboardMarkup(inline_keyboard=[]),
                kit_primary="#743D42",
                kit_secondary="#F2E9D8",
            )
            assert rendered == [] and len(message.photos) == 2
            # A stale file_id (e.g. after restart) falls back to same-face render.
            stale_message = make_message(fail_on=("uploaded-id",))
            query3 = make_query(player_callback(uuid4()), stale_message)
            await player_router._maybe_edit_portrait(
                query3,
                recipe,
                key,
                text="card",
                keyboard=InlineKeyboardMarkup(inline_keyboard=[]),
                kit_primary="#743D42",
                kit_secondary="#F2E9D8",
            )
            assert rendered == [("#743D42", "#F2E9D8")]
        finally:
            player_router.render_portrait_png = original
    finally:
        player_router.set_portrait_store(InMemoryPortraitFileCache())
