"""Atomic integer cash/reservations/arrears. Caller commits, never network I/O."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.domain.finances import MAX_MONEY, money
from goaletto.infrastructure.models import Club, FinanceAccount, LedgerEntry, MoneyReservation


class InsufficientFunds(ValueError):
    pass


@dataclass
class Account:
    club: Club
    record: FinanceAccount

    @property
    def available(self) -> int:
        return self.club.budget - self.record.reserved


async def locked_accounts(session: AsyncSession, *club_ids: UUID) -> dict[UUID, Account]:
    if not club_ids or len(set(club_ids)) != len(club_ids):
        raise ValueError("Unique account identities required")
    await allocation_lock(session)
    result = {}
    for club_id in sorted(club_ids):
        club = await session.scalar(
            select(Club)
            .where(Club.id == club_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        row = await session.scalar(
            select(FinanceAccount)
            .where(FinanceAccount.club_id == club_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if club is None or row is None:
            raise ValueError("Finance account not initialized")
        last = await session.scalar(
            select(LedgerEntry).where(
                LedgerEntry.club_id == club_id, LedgerEntry.sequence == row.sequence
            )
        )
        if last is None or (last.balance_after, last.reserved_after, last.arrears_after) != (
            club.budget,
            row.reserved,
            row.arrears,
        ):
            raise ValueError("Account does not reconcile with ledger")
        money(club.budget)
        money(row.reserved)
        money(row.arrears)
        if row.reserved > club.budget:
            raise ValueError("Reservations exceed cash")
        result[club_id] = Account(club, row)
    return result


def request(kind: str, amount: int, details: dict[str, Any] | None = None) -> dict[str, Any]:
    money(amount)
    if details is not None and not isinstance(details, dict):
        raise ValueError("Money details must be an object")
    return {"kind": kind, "amount": amount, "details": details or {}}


async def existing(session: AsyncSession, account: Account, key: str, body: dict[str, Any]) -> bool:
    if not isinstance(key, str) or not key or len(key) > 173:
        raise ValueError("Invalid money operation key")
    previous = await session.scalar(
        select(LedgerEntry).where(
            LedgerEntry.club_id == account.club.id, LedgerEntry.operation_key == key
        )
    )
    if previous is not None and previous.request != body:
        raise ValueError("Money operation reused with different scope or amount")
    return previous is not None


async def record(
    session: AsyncSession,
    account: Account,
    key: str,
    body: dict[str, Any],
    at: datetime,
    *,
    cash: int = 0,
    reserved: int = 0,
    arrears: int = 0,
) -> None:
    require_utc(at)
    if any(type(v) is not int or abs(v) > MAX_MONEY for v in (cash, reserved, arrears)):
        raise ValueError("Invalid ledger delta")
    balance = money(account.club.budget + cash)
    hold = money(account.record.reserved + reserved)
    debt = money(account.record.arrears + arrears)
    if hold > balance:
        raise InsufficientFunds("Reserved funds cannot be spent")
    if not any((cash, reserved, arrears)):
        raise ValueError("Empty money effect")
    account.record.sequence += 1
    account.club.budget, account.record.reserved, account.record.arrears = balance, hold, debt
    session.add(
        LedgerEntry(
            id=uuid5(account.club.id, "money:v1:" + key),
            club_id=account.club.id,
            operation_key=key,
            sequence=account.record.sequence,
            kind=body["kind"],
            cash_delta=cash,
            reserved_delta=reserved,
            arrears_delta=arrears,
            balance_after=balance,
            reserved_after=hold,
            arrears_after=debt,
            occurred_at=at,
            request=body,
        )
    )
    await session.flush()


async def repay_arrears(session: AsyncSession, account: Account, key: str, at: datetime) -> None:
    paid = min(account.available, account.record.arrears)
    if paid:
        body = request("arrears_paid", paid)
        await record(session, account, key + ":arrears", body, at, cash=-paid, arrears=-paid)


async def credit_locked(
    session: AsyncSession,
    account: Account,
    key: str,
    kind: str,
    amount: int,
    at: datetime,
    details: dict[str, Any] | None = None,
) -> bool:
    require_utc(at)
    if kind not in {"grant", "tickets", "sponsor", "merchandise", "prize", "transfer_in"}:
        raise ValueError("Unsupported credit kind")
    body = request(kind, amount, details)
    if await existing(session, account, key, body):
        return False
    if not amount:
        raise ValueError("Credit must be positive")
    await record(session, account, key, body, at, cash=amount)
    await repay_arrears(session, account, key, at)
    return True


async def debit_locked(
    session: AsyncSession,
    account: Account,
    key: str,
    kind: str,
    amount: int,
    at: datetime,
    *,
    mandatory: bool = False,
    details: dict[str, Any] | None = None,
) -> bool:
    require_utc(at)
    if type(mandatory) is not bool or kind not in {
        "player_salary",
        "staff_salary",
        "stadium_upkeep",
        "stadium_upgrade",
        "signing_fee",
    }:
        raise ValueError("Unsupported debit kind")
    body = request(kind, amount, {**(details or {}), "mandatory": mandatory})
    if await existing(session, account, key, body):
        return False
    if not amount:
        raise ValueError("Debit must be positive")
    if not mandatory and (account.record.arrears or account.available < amount):
        raise InsufficientFunds("Insufficient available funds or unpaid obligations")
    paid = min(account.available, amount)
    await record(session, account, key, body, at, cash=-paid, arrears=amount - paid)
    return True


async def reserve(
    session: AsyncSession,
    club_id: UUID,
    reservation_id: UUID,
    amount: int,
    purpose: dict[str, Any],
    at: datetime,
) -> MoneyReservation:
    require_utc(at)
    money(amount)
    if not amount or not isinstance(purpose, dict) or not purpose:
        raise ValueError("Reservation needs funds and purpose")
    account = (await locked_accounts(session, club_id))[club_id]
    row = await session.get(MoneyReservation, reservation_id)
    if row is not None:
        if (row.club_id, row.amount, row.purpose) != (club_id, amount, purpose):
            raise ValueError("Reservation identity reused")
        return row
    if account.record.arrears or account.available < amount:
        raise InsufficientFunds("Insufficient available funds or unpaid obligations")
    row = MoneyReservation(
        id=reservation_id,
        club_id=club_id,
        amount=amount,
        purpose=purpose,
        status="active",
        created_at=at,
        closed_at=None,
    )
    session.add(row)
    await record(
        session,
        account,
        f"reserve:{reservation_id}",
        request("reserve", amount, purpose),
        at,
        reserved=amount,
    )
    return row


async def release(session: AsyncSession, club_id: UUID, reservation_id: UUID, at: datetime) -> bool:
    require_utc(at)
    account = (await locked_accounts(session, club_id))[club_id]
    row = await session.scalar(
        select(MoneyReservation).where(MoneyReservation.id == reservation_id).with_for_update()
    )
    if row is None or row.club_id != club_id:
        raise ValueError("Unknown club reservation")
    if row.status == "released":
        return False
    if row.status != "active":
        raise ValueError("Settled reservation cannot be released")
    key = f"release:{reservation_id}"
    row.status, row.closed_at = "released", at
    await record(
        session, account, key, request("release", row.amount, row.purpose), at, reserved=-row.amount
    )
    await repay_arrears(session, account, key, at)
    return True


async def settle(
    session: AsyncSession, payer: UUID, receiver: UUID, reservation_id: UUID, at: datetime
) -> bool:
    require_utc(at)
    accounts = await locked_accounts(session, payer, receiver)
    row = await session.scalar(
        select(MoneyReservation).where(MoneyReservation.id == reservation_id).with_for_update()
    )
    if row is None or row.club_id != payer:
        raise ValueError("Unknown payer reservation")
    body = request(
        "transfer_out", row.amount, {"receiver": str(receiver), "reservation": str(row.id)}
    )
    key = f"settle:{reservation_id}"
    if await existing(session, accounts[payer], key, body):
        if row.status != "settled":
            raise ValueError("Settlement state disagrees")
        return False
    if row.status != "active":
        raise ValueError("Reservation is no longer active")
    row.status, row.closed_at = "settled", at
    await record(session, accounts[payer], key, body, at, cash=-row.amount, reserved=-row.amount)
    await credit_locked(
        session,
        accounts[receiver],
        key,
        "transfer_in",
        row.amount,
        at,
        {"payer": str(payer), "reservation": str(row.id)},
    )
    return True
