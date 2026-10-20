"""One final credit assessment inside each AsyncSession commit, after all asset movements."""

from datetime import UTC, datetime

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import async_session
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import get_history
from sqlalchemy.util.concurrency import await_only

from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import (
    Club,
    FinanceAccount,
    GameActionReceipt,
    LedgerEntry,
    Person,
    Player,
    StadiumExpansion,
    Staff,
    StaffQualification,
)


@event.listens_for(Session, "before_flush")
def collect(session: Session, flush_context: object, instances: object) -> None:
    clubs = session.info.setdefault("economy_clubs", set())
    people = session.info.setdefault("economy_people", set())
    for row in (*session.new, *session.dirty, *session.deleted):
        if isinstance(row, Club):
            if row.id is not None:
                clubs.add(row.id)
        elif isinstance(row, (Player, Staff, StadiumExpansion, FinanceAccount)):
            if row.club_id is not None:
                clubs.add(row.club_id)
            if isinstance(row, (Player, Staff)):
                clubs.update(v for v in get_history(row, "club_id").deleted if v is not None)
        elif isinstance(row, (Person, StaffQualification)):
            people.add(row.id if isinstance(row, Person) else row.person_id)
        if isinstance(row, (GameActionReceipt, LedgerEntry)):
            at = row.occurred_at
            if at is not None:
                session.info["economy_at"] = max(at, session.info.get("economy_at", at))

    if (clubs or people) and async_session(session) is not None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
        )


async def finalize(session: Session) -> None:
    asynchronous = async_session(session)
    if asynchronous is None:
        return
    await asynchronous.flush()
    people = session.info.pop("economy_people", set())
    clubs = session.info.pop("economy_clubs", set())
    if people:
        clubs.update(
            v
            for v in await asynchronous.scalars(
                select(Player.club_id).where(Player.person_id.in_(people))
            )
            if v is not None
        )
        clubs.update(
            v
            for v in await asynchronous.scalars(
                select(Staff.club_id).where(Staff.person_id.in_(people))
            )
            if v is not None
        )
    if not clubs:
        return
    from goaletto.application.insolvency import assess

    at = session.info.get("economy_at") or datetime.now(UTC)
    for club in sorted(clubs):
        await assess(asynchronous, club, at)
    await asynchronous.flush()


@event.listens_for(Session, "before_commit")
def before_commit(session: Session) -> None:
    if not session.in_nested_transaction() and async_session(session) is not None:
        await_only(finalize(session))


@event.listens_for(Session, "after_transaction_end")
def clear(session: Session, transaction: object) -> None:
    if getattr(transaction, "parent", None) is None:
        for key in ("economy_people", "economy_clubs", "economy_at"):
            session.info.pop(key, None)
