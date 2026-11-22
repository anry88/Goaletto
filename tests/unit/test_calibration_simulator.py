"""Calibration guards and an intentionally favourable weak-player stress case."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from goaletto.application.cosmetics import initial_rename
from goaletto.config import Settings
from goaletto.presentation.channels import channel_links
from goaletto.presentation.i18n import using_locale
from scripts.simulate_ten_seasons import local_target, weak_player_stress


def test_calibration_refuses_remote_production_and_other_databases():
    for environment, url in (
        ("production", "postgresql+asyncpg://x:x@127.0.0.1:5433/goaletto"),
        ("development", "postgresql+asyncpg://x:x@remote-db.invalid:5433/goaletto"),
        ("development", "postgresql+asyncpg://x:x@127.0.0.1:5433/riverking"),
    ):
        with pytest.raises(ValueError):
            local_target(Settings(environment=environment, database_url=SecretStr(url)))


def test_weak_player_cannot_jump_to_twenty_in_two_seasons_even_with_elite_staff():
    rows = weak_player_stress()
    assert rows[1]["skills"]["defending"] == 6
    assert rows[-1]["skills"]["defending"] == 18
    assert all(r["budget"]["defending"] <= 15000 for r in rows)
    assert all(r["budget"]["match"] <= 10000 and r["budget"]["training"] <= 20000 for r in rows)


def test_initial_rename_exception_cannot_unlock_paid_or_vip_rights():
    owner = uuid4()
    choice = SimpleNamespace(declined_at=None)
    grant = SimpleNamespace(origin="initial", ownership_id=owner, status="available")
    assert initial_rename(grant, choice, owner)
    choice.declined_at = datetime(2030, 1, 1, tzinfo=UTC)
    assert not initial_rename(grant, choice, owner)
    choice.declined_at = None
    grant.origin = "stars"
    assert not initial_rename(grant, choice, owner)
    grant.origin = "vip"
    assert not initial_rename(grant, choice, owner)


def test_start_help_channels_are_present_in_both_languages():
    for language in ("ru", "en"):
        with using_locale(language):
            text = channel_links()
            assert 'href="https://t.me/goaletto_ru"' in text
            assert 'href="https://t.me/goaletto_en"' in text


async def test_payment_support_copies_command_without_exposing_uuid_in_list():
    from unittest.mock import AsyncMock

    from goaletto.bot.payments_router import purchase_list
    from goaletto.infrastructure.models import StarPayment

    key = uuid4()
    paid = StarPayment(
        id=key,
        product="vip",
        amount=1,
        currency="XTR",
        received_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [paid])),
    )
    body, keyboard = await purchase_list(session, 123)
    assert str(key) not in body and "VIP статус" in body
    assert keyboard.inline_keyboard[0][0].copy_text.text == f"/paysupport {key} "
