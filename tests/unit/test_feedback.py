"""Voluntary feedback unit checks. Fake Telegram/database, no network or reset."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import EVENT_SPECS, FEEDBACK_REASONS, JourneyEvent, Origin
from goaletto.application.feedback import (
    _REASON_TO_ENUM,
    COMMENT_MAX_CHARS,
    COMMENT_WINDOW,
    COOLDOWN_MIN_DAYS,
    REASON_CODES,
    FeedbackSettings,
)
from goaletto.application.feedback import (
    save_comment as _save_comment,
)
from goaletto.bot import feedback_router, feedback_views, live_views
from goaletto.presentation.i18n import using_locale

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def test_reason_catalog_matches_contract_and_buttons_fit_telegram() -> None:
    assert REASON_CODES == (
        "unclear_next_step",
        "difficult_lineup",
        "long_wait",
        "inconvenient_time",
        "uninteresting_game",
        "technical_problem",
        "other",
    )
    assert set(_REASON_TO_ENUM.values()) == set(FEEDBACK_REASONS)
    assert _REASON_TO_ENUM["technical_problem"].value == "technical_failure"
    response = uuid4()
    for index in range(len(REASON_CODES)):
        callback = feedback_views.reason_callback(response, index)
        assert len(callback.encode()) <= 64
        action, parsed, arg = feedback_views.parse_callback(callback)
        assert (action, parsed, arg) == ("r", response, index)
    for builder in (
        feedback_views.skip_callback,
        feedback_views.optout_callback,
        feedback_views.comment_skip_callback,
    ):
        assert len(builder(response).encode()) <= 64
    assert len(feedback_views.OPEN_CALLBACK.encode()) <= 64
    assert feedback_views.parse_callback(feedback_views.OPEN_CALLBACK) == ("open", None, None)


def test_stale_and_foreign_callbacks_are_rejected() -> None:
    with pytest.raises(ValueError, match="Stale|Invalid"):
        feedback_views.parse_callback("fb:r:bad:0")
    with pytest.raises(ValueError, match="Stale|Invalid"):
        feedback_views.parse_callback("fb:r:AAAAAAAAAAAAAAAAAAAAAA:99")
    with pytest.raises(ValueError, match="Stale"):
        feedback_views.parse_callback("fb:x:AAAAAAAAAAAAAAAAAAAAAA")
    with pytest.raises(ValueError, match="Invalid|Stale"):
        feedback_views.parse_callback("lv:p:1:AAAAAAAAAAAAAAAAAAAAAA:AAAAAAAAAAAAAAAAAAAAAA:1n")


def test_ru_en_strings_cover_feedback_without_technical_details() -> None:
    table = json.loads(Path("src/goaletto/presentation/en.json").read_text())
    required = [
        "Непонятно, что дальше",
        "Сложно настроить состав",
        "Долго ждать матч",
        "Неудобное время",
        "Неинтересная игра",
        "Техническая проблема",
        "Другое",
        "Пропустить",
        "Не спрашивать больше",
        "✍️ Оставить отзыв",
        "Спасибо за отзыв! Он поможет сделать игру понятнее.",
        "Комментарий слишком длинный: максимум 500 символов.",
    ]
    for key in required:
        assert key in table and table[key] and table[key] != key
    with using_locale("en"):
        assert feedback_views.REASON_LABELS["long_wait"] == "Long wait for the match"
    with using_locale("ru"):
        assert feedback_views.REASON_LABELS["long_wait"] == "Долго ждать матч"
    text, keyboard = feedback_views.prompt_view(uuid4())
    assert "640" not in text and "тик" not in text.lower()
    assert len(keyboard.inline_keyboard) == 9


def test_feedback_settings_require_explicit_retention_and_min_cooldown() -> None:
    with pytest.raises(ValueError, match="retention|collection"):
        FeedbackSettings(enabled=True, comment_retention_days=None, cooldown_days=7)
    with pytest.raises(ValueError, match="[Cc]ooldown"):
        FeedbackSettings(enabled=True, comment_retention_days=30, cooldown_days=6)
    assert COOLDOWN_MIN_DAYS == 7
    assert COMMENT_WINDOW == timedelta(minutes=30)
    assert COMMENT_MAX_CHARS == 500
    settings = FeedbackSettings(enabled=True, comment_retention_days=30, cooldown_days=7)
    assert settings.enabled and settings.comment_retention_days == 30


def test_feedback_is_activity_without_ownership_extension() -> None:
    assert EVENT_SPECS["feedback_answered"].user_activity
    assert not EVENT_SPECS["feedback_answered"].ownership_activity
    assert EVENT_SPECS["feedback_skipped"].user_activity
    assert not EVENT_SPECS["feedback_skipped"].ownership_activity
    assert not EVENT_SPECS["feedback_prompt_delivered"].user_activity


def test_comment_envelope_never_carries_free_text() -> None:
    event = JourneyEvent(
        event_id=uuid4(),
        name="feedback_answered",
        occurred_at=NOW,
        recorded_at=NOW,
        origin=Origin.USER,
        operation_id=uuid4(),
        effect_number=0,
        surface=__import__("goaletto.application.analytics", fromlist=["Surface"]).Surface.FEEDBACK,
        analytics_manager_id=uuid4(),
        session_id=uuid4(),
        feedback_campaign_id=uuid4(),
        reason=_REASON_TO_ENUM["other"],
    )
    from goaletto.infrastructure.analytics import event_envelope

    envelope = event_envelope(event)
    assert "text" not in envelope and "comment" not in envelope
    assert envelope["reason"] == "other"


class _FakeSession:
    def in_transaction(self) -> bool:
        return True


async def test_comment_length_boundary_rejects_without_echo() -> None:
    secret = "x" * 501
    with pytest.raises(ValueError, match="1..500") as error:
        await _save_comment(
            cast(AsyncSession, _FakeSession()),
            cast(object, None),  # type: ignore[arg-type]
            response_id=uuid4(),
            text=secret,
            at=NOW,
        )
    assert secret not in str(error.value)
    exact = "y" * 500
    # Length passes validation; the call then fails on the actor, still without echo.
    with pytest.raises(Exception) as failed:
        await _save_comment(
            cast(AsyncSession, _FakeSession()),
            cast(object, None),  # type: ignore[arg-type]
            response_id=uuid4(),
            text=exact,
            at=NOW,
        )
    assert exact not in str(failed.value)


def test_final_live_screen_keeps_report_primary() -> None:
    from goaletto.application.live_commands import LiveScreen
    from goaletto.application.match_simulation import demo_match, simulate_match
    from goaletto.domain.match_types import Side

    final, _ = simulate_match(demo_match(7))
    assert final.phase.value == "full_time"
    screen = LiveScreen(
        ownership_id=uuid4(),
        club_id=uuid4(),
        club_name="Club",
        fixture_id=uuid4(),
        match_id=uuid4(),
        state=final,
        projected=final,
        side=Side.HOME,
        target=None,
        halftime=False,
        deadline=None,
        open=False,
        recent=(),
    )
    text, keyboard = live_views.render(screen, feedback_available=True)
    assert "Матч завершён" in text or "finished" in text.lower() or "🏁" in text
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    report = next(i for i, c in enumerate(callbacks) if c and c.startswith("hx:m:"))
    feedback = next(i for i, c in enumerate(callbacks) if c == "fb:open")
    assert report < feedback


class _FakeDatabase:
    def __init__(self) -> None:
        self.depth = 0

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        assert self.depth == 0
        self.depth += 1
        try:
            yield cast(AsyncSession, _FakeSession())
        finally:
            self.depth -= 1


class _FakeMessage:
    photo = None

    def __init__(self, db: _FakeDatabase, text: str = "/feedback") -> None:
        self.db = db
        self.from_user = SimpleNamespace(id=123)
        self.chat = SimpleNamespace(type="private")
        self.text = text
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup: object = None) -> SimpleNamespace:
        assert self.db.depth == 0, "Telegram send while transaction is open"
        self.answers.append((text, reply_markup))
        return SimpleNamespace(message_id=1)


async def test_feedback_command_commits_before_telegram_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDatabase()
    message = _FakeMessage(db)
    update = SimpleNamespace(update_id=41)
    response_id = uuid4()
    campaign_id = uuid4()

    async def fake_open(session: object, actor: object, **kwargs: object) -> object:
        assert cast(_FakeSession, session).in_transaction()
        assert db.depth == 1
        return SimpleNamespace(response_id=response_id, campaign_id=campaign_id, status="prompted")

    async def fake_record(session: object, **kwargs: object) -> bool:
        assert db.depth == 1
        return True

    async def fake_actor(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(manager_id=uuid4())

    monkeypatch.setattr(feedback_router, "_open", fake_open)
    monkeypatch.setattr(feedback_router, "record_prompt_delivered", fake_record)
    monkeypatch.setattr(feedback_router, "actor_for", fake_actor)

    async def fake_render(database: object, rid: object, status: object, *a: object) -> object:
        return ("prompt-text", None)

    monkeypatch.setattr(feedback_router, "_render_for_status", fake_render)

    async def no_automatic(*args, **kwargs):
        return None

    monkeypatch.setattr(feedback_router, "resume_automatic_prompt", no_automatic)
    await feedback_router.feedback_command(
        cast(object, message),
        cast(object, db),
        cast(object, update),  # type: ignore[arg-type]
    )
    assert len(message.answers) == 1 and message.answers[0][0] == "prompt-text"


async def test_comment_route_ignores_commands_and_messages_without_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDatabase()

    async def fake_actor(*args: object, **kwargs: object) -> object:
        return SimpleNamespace(manager_id=uuid4())

    monkeypatch.setattr(feedback_router, "actor_for", fake_actor)

    async def no_window(session: object, manager_id: object, at: object) -> None:
        return None

    monkeypatch.setattr(feedback_router, "find_open_comment", no_window)
    plain = _FakeMessage(db, text="hello ordinary message")
    from aiogram.dispatcher.event.bases import SkipHandler

    with pytest.raises(SkipHandler):
        await feedback_router.feedback_comment_message(
            cast(object, plain),
            cast(object, db),
            cast(object, SimpleNamespace(update_id=5)),  # type: ignore[arg-type]
        )
    assert plain.answers == []
