"""Initial persistence schema: league topology and durable work primitives."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class LeagueSet(Base):
    __tablename__ = "league_sets"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class League(Base):
    __tablename__ = "leagues"
    __table_args__ = (
        UniqueConstraint("league_set_id", "tier", name="uq_league_set_tier"),
        CheckConstraint("tier BETWEEN 1 AND 5", name="ck_league_tier"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"), index=True)
    tier: Mapped[int] = mapped_column(Integer)


class Club(Base):
    __tablename__ = "clubs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    league_id: Mapped[UUID] = mapped_column(ForeignKey("leagues.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # NULL identifies an unclaimed AI club. PostgreSQL allows multiple NULL owners.
    owner_telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    budget: Mapped[int] = mapped_column(BigInteger, default=0)
    __table_args__ = (CheckConstraint("budget >= 0", name="ck_club_budget"),)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_job_status"),
        CheckConstraint("attempts >= 0", name="ck_job_attempts"),
        Index("ix_jobs_due", "status", "due_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200))


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_outbox_status"),
        Index("ix_outbox_due", "status", "due_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Delivery dispatcher with leases/retry_after is still a separate backlog item.
