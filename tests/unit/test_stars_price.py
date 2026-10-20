"""A changed price blocks stale invoices but preserves already approved receipts."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from goaletto.application import stars
from goaletto.infrastructure.models import StarOrder

NOW = datetime(2030, 1, 1, tzinfo=UTC)


async def test_new_vip_price_and_stale_precheckout(monkeypatch):
    assert stars.PRICES == {"vip": 100, "rename": 50}
    row = StarOrder(
        id=uuid4(),
        payer_id=123,
        product="vip",
        amount=1,
        currency="XTR",
        status="created",
        expires_at=NOW + timedelta(minutes=30),
    )
    session = SimpleNamespace(get=AsyncMock(return_value=row))
    monkeypatch.setattr(stars, "allocation_lock", AsyncMock())
    assert not await stars.precheckout(session, stars.payload(row), 123, 1, "XTR", "old", NOW)


async def test_created_old_quote_is_cancelled_instead_of_reused(monkeypatch):
    owner = SimpleNamespace(id=uuid4())
    club = SimpleNamespace(id=uuid4())
    row = StarOrder(id=uuid4(), product="vip", amount=1, status="created")
    monkeypatch.setattr(stars, "owned_club", AsyncMock(return_value=(owner, club)))
    monkeypatch.setattr(stars, "ensure_initial", AsyncMock())
    monkeypatch.setattr(stars, "ensure_vip_bonus", AsyncMock())
    monkeypatch.setattr(stars, "eligible", AsyncMock(return_value=True))
    added = []
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=row),
        get=AsyncMock(return_value=None),
        flush=AsyncMock(),
        add=added.append,
    )
    actor = SimpleNamespace(manager_id=uuid4(), telegram_id=123, operation_id=uuid4(), at=NOW)
    order = await stars.create_order(session, actor, "vip", owner.id)
    assert row.status == "cancelled" and order.amount == 100
    assert added == [order]


async def test_already_approved_quote_keeps_its_terms(monkeypatch):
    owner = SimpleNamespace(id=uuid4())
    row = StarOrder(id=uuid4(), product="vip", amount=1, status="approved")
    monkeypatch.setattr(
        stars, "owned_club", AsyncMock(return_value=(owner, SimpleNamespace(id=uuid4())))
    )
    monkeypatch.setattr(stars, "ensure_initial", AsyncMock())
    monkeypatch.setattr(stars, "ensure_vip_bonus", AsyncMock())
    session = SimpleNamespace(scalar=AsyncMock(return_value=row))
    actor = SimpleNamespace(at=NOW)
    with pytest.raises(ValueError, match="approved payment"):
        await stars.create_order(session, actor, "vip", owner.id)
    assert row.status == "approved" and row.amount == 1


@pytest.mark.parametrize("amount", [1, 100])
async def test_successful_payment_honours_approved_quote_after_price_change(monkeypatch, amount):
    row = StarOrder(
        id=uuid4(),
        club_id=uuid4(),
        product="vip",
        amount=amount,
        payer_id=123,
        currency="XTR",
        status="approved",
    )
    monkeypatch.setattr(stars, "allocation_lock", AsyncMock())
    monkeypatch.setattr(stars, "eligible", AsyncMock(return_value=True))
    monkeypatch.setattr(stars, "ensure_vip_bonus", AsyncMock())
    notices = []
    monkeypatch.setattr(stars, "notice", lambda *args: notices.append(args))
    added = []
    session = SimpleNamespace(
        info={},
        scalar=AsyncMock(return_value=None),
        get=AsyncMock(return_value=row),
        flush=AsyncMock(),
        add=added.append,
    )
    paid = await stars.successful(
        session,
        value=stars.payload(row),
        payer=123,
        amount=amount,
        currency="XTR",
        charge="approved-quote",
        at=NOW,
    )
    assert paid.status == "delivered" and paid.amount == amount
    assert row.status == "paid" and row.amount == amount
    assert paid.vip_until == datetime(2030, 2, 1, tzinfo=UTC)
    assert added == [paid] and len(notices) == 1
