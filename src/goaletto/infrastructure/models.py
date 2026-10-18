"""Persistence for the game foundation; schema does not execute gameplay."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
        UniqueConstraint("id", "league_set_id", name="uq_league_id_set"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"), index=True)
    tier: Mapped[int] = mapped_column(Integer)


class Club(Base):
    __tablename__ = "clubs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    league_id: Mapped[UUID] = mapped_column(ForeignKey("leagues.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    # Compatibility projection; ownership_periods is the authoritative history.
    owner_telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    budget: Mapped[int] = mapped_column(BigInteger, default=0)
    kit_primary: Mapped[str] = mapped_column(String(7), server_default="#743D42")
    kit_secondary: Mapped[str] = mapped_column(String(7), server_default="#F2E9D8")
    kit_version: Mapped[int] = mapped_column(Integer, server_default="1")
    stadium_capacity: Mapped[int] = mapped_column(Integer, server_default="5000")
    supporters: Mapped[int] = mapped_column(Integer, server_default="2500")
    academy_level: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    __table_args__ = (
        CheckConstraint("budget >= 0", name="ck_club_budget"),
        CheckConstraint("kit_primary ~ '^#[0-9A-Fa-f]{6}$'", name="ck_club_primary_color"),
        CheckConstraint("kit_secondary ~ '^#[0-9A-Fa-f]{6}$'", name="ck_club_secondary_color"),
        CheckConstraint("kit_version > 0", name="ck_club_kit_version"),
        CheckConstraint("stadium_capacity >= 0 AND supporters >= 0", name="ck_club_crowd"),
        CheckConstraint("academy_level BETWEEN 1 AND 5", name="ck_club_academy_level"),
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('held', 'pending', 'done', 'failed')", name="ck_job_status"),
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
        CheckConstraint("priority BETWEEN 0 AND 100", name="ck_outbox_priority"),
        CheckConstraint("attempts >= 0", name="ck_outbox_attempts"),
        CheckConstraint("(leased_until IS NULL) = (lease_owner IS NULL)", name="ck_outbox_lease"),
        CheckConstraint(
            "telegram_message_id IS NULL OR telegram_message_id > 0",
            name="ck_outbox_message_id",
        ),
        Index("ix_outbox_due", "status", "due_at"),
        Index("ix_outbox_delivery", "status", "priority", "due_at"),
        Index("ix_outbox_lease", "status", "leased_until"),
        Index("ix_outbox_live_board", "live_board_key"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Short database leases; Telegram calls never hold these row locks.
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    live_board_key: Mapped[str | None] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(80))
    enqueued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class OutboxBoard(Base):
    """One durable message and delivery lease per fixture/ownership stream."""

    __tablename__ = "outbox_boards"
    __table_args__ = (
        UniqueConstraint("fixture_id", "ownership_id", name="uq_outbox_board_stream"),
        CheckConstraint(
            "telegram_message_id IS NULL OR telegram_message_id > 0",
            name="ck_board_message_id",
        ),
        CheckConstraint("revision BETWEEN -1 AND 1000", name="ck_board_revision"),
        CheckConstraint("(leased_until IS NULL) = (lease_owner IS NULL)", name="ck_board_lease"),
    )
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(ForeignKey("fixtures.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(SmallInteger, server_default="-1")
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxDeliveryAttempt(Base):
    """Operational history independent of journey retention/analytics opt-out."""

    __tablename__ = "outbox_delivery_attempts"
    __table_args__ = (
        Index("ix_delivery_attempt_started", "started_at"),
        CheckConstraint("attempt > 0", name="ck_delivery_attempt_number"),
        CheckConstraint(
            "outcome IN ('claimed','sending','sent','edited','duplicate','rate_limited',"
            "'transient','unknown', 'blocked','chat_unavailable','invalid_request',"
            "'skip_stale','skip_unavailable')",
            name="ck_delivery_outcome",
        ),
        CheckConstraint(
            "(outcome IN ('claimed','sending')) = (finished_at IS NULL)",
            name="ck_delivery_finished",
        ),
        CheckConstraint(
            "(network_started_at IS NULL OR network_started_at >= started_at) AND "
            "(finished_at IS NULL OR finished_at >= started_at) AND "
            "(finished_at IS NULL OR network_started_at IS NULL OR "
            "finished_at >= network_started_at)",
            name="ck_delivery_times",
        ),
    )
    outbox_id: Mapped[UUID] = mapped_column(ForeignKey("outbox_messages.id"), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_owner: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    render_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    network_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(24))


class RuntimeRequest(Base):
    """One operational attempt, no Telegram IDs, payloads or journey identity."""

    __tablename__ = "runtime_requests"
    __table_args__ = (
        Index("ix_runtime_request_time", "received_at"),
        Index("ix_runtime_request_operation", "operation_id"),
        CheckConstraint(
            "route IN ('match_open','match_callback','history')", name="ck_request_route"
        ),
        CheckConstraint(
            "business_outcome IN ('unknown','committed','rejected')", name="ck_request_business"
        ),
        CheckConstraint(
            "delivery_outcome IN ('not_started','sending','accepted','rejected','unknown')",
            name="ck_request_delivery",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= received_at", name="ck_request_time"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    route: Mapped[str] = mapped_column(String(24))
    fixture_id: Mapped[UUID | None] = mapped_column(ForeignKey("fixtures.id"))
    command_id: Mapped[UUID | None] = mapped_column(ForeignKey("accepted_commands.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    render_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    network_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    business_outcome: Mapped[str] = mapped_column(String(16))
    delivery_outcome: Mapped[str] = mapped_column(String(16))


class Manager(Base):
    __tablename__ = "managers"
    __table_args__ = (
        CheckConstraint("telegram_id > 0", name="ck_manager_telegram_id"),
        CheckConstraint(
            "preferred_kickoff_minute BETWEEN 0 AND 1439", name="ck_manager_kickoff_minute"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    locale: Mapped[str] = mapped_column(String(16), server_default="ru")
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    timezone_name: Mapped[str] = mapped_column(String(64), server_default="UTC")
    preferred_kickoff_minute: Mapped[int | None] = mapped_column(SmallInteger)
    # NULL for legacy/imported managers; preview /start does not create a launch cohort.
    first_game_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    bot_available: Mapped[bool] = mapped_column(Boolean, server_default="true")
    bot_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KickoffPreferenceReceipt(Base):
    __tablename__ = "kickoff_preference_receipts"
    __table_args__ = (
        CheckConstraint(
            "timezone_name IS NOT NULL OR preferred_minute IS NOT NULL",
            name="ck_kickoff_receipt_value",
        ),
        CheckConstraint("preferred_minute BETWEEN 0 AND 1439", name="ck_kickoff_receipt_minute"),
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    manager_id: Mapped[UUID] = mapped_column(ForeignKey("managers.id"))
    timezone_name: Mapped[str | None] = mapped_column(String(64))
    preferred_minute: Mapped[int | None] = mapped_column(SmallInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KickoffDecisionReceipt(Base):
    """Immutable announced decision, also usable before a planned fixture is materialized."""

    __tablename__ = "kickoff_decision_receipts"
    __table_args__ = (
        CheckConstraint("seed >= 0", name="ck_kickoff_decision_seed"),
        CheckConstraint("tie_break IN (0, 1)", name="ck_kickoff_decision_tie"),
        CheckConstraint(
            "(kickoff AT TIME ZONE 'UTC')::date = assigned_day", name="ck_kickoff_decision_day"
        ),
    )
    fixture_key: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    home_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    away_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    assigned_day: Mapped[date] = mapped_column(Date)
    seed: Mapped[int] = mapped_column(BigInteger)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_version: Mapped[str] = mapped_column(String(40))
    tie_break: Mapped[int | None] = mapped_column(SmallInteger)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnalyticsManager(Base):
    """Restricted link to Telegram-bearing managers, separate from journey exports."""

    __tablename__ = "analytics_managers"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    manager_id: Mapped[UUID] = mapped_column(
        ForeignKey("managers.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OwnershipPeriod(Base):
    __tablename__ = "ownership_periods"
    __table_args__ = (
        UniqueConstraint("id", "manager_id", "club_id", name="uq_ownership_identity"),
        CheckConstraint("last_game_action_at >= claimed_at", name="ck_ownership_last_action"),
        CheckConstraint("expires_at > last_game_action_at", name="ck_ownership_deadline"),
        CheckConstraint(
            "expires_at = ((last_game_action_at AT TIME ZONE 'UTC') + INTERVAL '6 months') "
            "AT TIME ZONE 'UTC'",
            name="ck_ownership_calendar_deadline",
        ),
        CheckConstraint("ended_at IS NULL OR ended_at >= claimed_at", name="ck_ownership_end"),
        CheckConstraint(
            "(ended_at IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND end_reason IS NOT NULL AND "
            "end_reason IN ('inactive', 'operator', 'voluntary'))",
            name="ck_ownership_end_reason",
        ),
        Index(
            "uq_ownership_active_club",
            "club_id",
            unique=True,
            postgresql_where="ended_at IS NULL",
        ),
        Index(
            "uq_ownership_active_manager",
            "manager_id",
            unique=True,
            postgresql_where="ended_at IS NULL",
        ),
        Index("ix_ownership_expiry", "expires_at", postgresql_where="ended_at IS NULL"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    operation_key: Mapped[str] = mapped_column(String(200), unique=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"), index=True)
    manager_id: Mapped[UUID] = mapped_column(ForeignKey("managers.id"), index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_game_action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(String(16))


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("league_set_id", "number", name="uq_season_set_number"),
        UniqueConstraint("id", "league_set_id", name="uq_season_id_set"),
        CheckConstraint("number > 0", name="ck_season_number"),
        CheckConstraint(
            "status IN ('draft', 'scheduled', 'active', 'finished', 'rolled_over')",
            name="ck_season_status",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_season_dates"),
        CheckConstraint(
            "(status = 'rolled_over') = (rollover_completed_at IS NOT NULL)",
            name="ck_season_rollover",
        ),
        Index(
            "uq_season_active_set",
            "league_set_id",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    seed: Mapped[int] = mapped_column(BigInteger)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calendar_policy: Mapped[str] = mapped_column(String(40), server_default="four-day-calendar-v1")
    calendar_enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    cycle_epoch: Mapped[date | None] = mapped_column(Date)
    rollover_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SeasonRolloverReceipt(Base):
    """Immutable proof of the entire committed offseason transition."""

    __tablename__ = "season_rollover_receipts"
    __table_args__ = (
        CheckConstraint("season_id <> next_season_id", name="ck_rollover_distinct_seasons"),
        CheckConstraint("policy_version = 'season-rollover-v1'", name="ck_rollover_policy"),
        CheckConstraint(
            "jsonb_typeof(counts) = 'object' AND jsonb_typeof(movements) = 'object'",
            name="ck_rollover_objects",
        ),
    )
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    next_season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"), unique=True)
    policy_version: Mapped[str] = mapped_column(String(80))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    movements: Mapped[dict[str, str]] = mapped_column(JSONB)


class SeasonClub(Base):
    """Historical membership; never inferred from a club's current league."""

    __tablename__ = "season_clubs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season_id", "league_set_id"],
            ["seasons.id", "seasons.league_set_id"],
            name="fk_membership_season_set",
        ),
        ForeignKeyConstraint(
            ["league_id", "league_set_id"],
            ["leagues.id", "leagues.league_set_id"],
            name="fk_membership_league_set",
        ),
        UniqueConstraint("season_id", "club_id", "league_id", name="uq_membership_club_league"),
        UniqueConstraint("season_id", "league_id", "tie_break_order", name="uq_membership_draw"),
        CheckConstraint("tie_break_order BETWEEN 1 AND 10", name="ck_membership_draw"),
        Index("ix_membership_league", "season_id", "league_id"),
        Index("ix_membership_club", "club_id"),
    )
    season_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"), primary_key=True)
    league_id: Mapped[UUID] = mapped_column(Uuid)
    league_set_id: Mapped[UUID] = mapped_column(Uuid)
    tie_break_order: Mapped[int] = mapped_column(SmallInteger)


class AppearanceIdentity(Base):
    __tablename__ = "appearance_identities"
    __table_args__ = (
        CheckConstraint("origin_bank IN ('players', 'staff')", name="ck_appearance_bank"),
        CheckConstraint("catalog_version > 0", name="ck_appearance_catalog"),
    )
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    origin_bank: Mapped[str] = mapped_column(String(16), index=True)
    catalog_version: Mapped[int] = mapped_column(Integer)


class AppearanceVariant(Base):
    __tablename__ = "appearance_variants"
    __table_args__ = (
        UniqueConstraint("id", "identity_id", name="uq_variant_identity"),
        CheckConstraint("bank IN ('players', 'staff')", name="ck_variant_bank"),
    )
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("appearance_identities.id"), index=True)
    bank: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str | None] = mapped_column(String(32))
    asset_path: Mapped[str] = mapped_column(String(240))
    mask_path: Mapped[str] = mapped_column(String(240))


class Person(Base):
    __tablename__ = "persons"
    __table_args__ = (
        ForeignKeyConstraint(
            ["appearance_variant_id", "appearance_identity_id"],
            ["appearance_variants.id", "appearance_variants.identity_id"],
            name="fk_person_same_face_variant",
        ),
        CheckConstraint("age BETWEEN 0 AND 150", name="ck_person_age"),
        CheckConstraint("career IN ('player', 'staff', 'retired')", name="ck_person_career"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    creation_key: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    age: Mapped[int] = mapped_column(SmallInteger)
    career: Mapped[str] = mapped_column(String(16))
    appearance_identity_id: Mapped[str] = mapped_column(
        ForeignKey("appearance_identities.id"), index=True
    )
    appearance_variant_id: Mapped[str] = mapped_column(String(100))
    # Nullable until the home-calendar policy is decided; no second staff age counter.
    home_league_set_id: Mapped[UUID | None] = mapped_column(ForeignKey("league_sets.id"))
    age_applied_for_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))


POSITIONS_SQL = "'GK','DL','DC','DR','DM','ML','MC','MR','AMC','AML','AMR','FC'"
STAFF_ROLES_SQL = (
    "'head_coach','goalkeeping_coach','defence_coach','attack_coach',"
    "'fitness_coach','physiotherapist','scout'"
)


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("person_id", "club_id", name="uq_player_person_club"),
        CheckConstraint(f"position IN ({POSITIONS_SQL})", name="ck_player_position"),
        CheckConstraint(
            f"cardinality(alternative_positions) <= 2 AND "
            f"alternative_positions <@ ARRAY[{POSITIONS_SQL}]::varchar[] AND "
            "NOT position = ANY(alternative_positions) AND "
            "array_position(alternative_positions, NULL) IS NULL AND "
            "(cardinality(alternative_positions) < 2 OR "
            "alternative_positions[1] <> alternative_positions[2])",
            name="ck_player_alternatives",
        ),
        *(
            CheckConstraint(f"{skill} BETWEEN 1 AND 20", name=f"ck_player_{skill}")
            for skill in (
                "goalkeeping",
                "defending",
                "playmaking",
                "winger",
                "attacking",
                "set_pieces",
                "stamina",
                "pace",
                "leadership",
                "loyalty",
            )
        ),
        CheckConstraint("form BETWEEN 1 AND 8", name="ck_player_form"),
        CheckConstraint("experience >= 0", name="ck_player_experience"),
        CheckConstraint("morale BETWEEN 0 AND 100", name="ck_player_morale"),
        CheckConstraint("status IN ('active', 'last_season', 'retired')", name="ck_player_status"),
        CheckConstraint(
            "(status = 'active' AND last_season_id IS NULL AND retired_after_season_id IS NULL) OR "
            "(status = 'last_season' AND last_season_id IS NOT NULL AND "
            "retired_after_season_id IS NULL) OR (status = 'retired' AND "
            "last_season_id IS NOT NULL AND retired_after_season_id IS NOT NULL AND "
            "retired_after_season_id = last_season_id "
            "AND club_id IS NULL)",
            name="ck_player_retirement",
        ),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)
    club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), index=True)
    position: Mapped[str] = mapped_column(String(3))
    alternative_positions: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    goalkeeping: Mapped[int] = mapped_column(SmallInteger)
    defending: Mapped[int] = mapped_column(SmallInteger)
    playmaking: Mapped[int] = mapped_column(SmallInteger)
    winger: Mapped[int] = mapped_column(SmallInteger)
    attacking: Mapped[int] = mapped_column(SmallInteger)
    set_pieces: Mapped[int] = mapped_column(SmallInteger)
    stamina: Mapped[int] = mapped_column(SmallInteger)
    pace: Mapped[int] = mapped_column(SmallInteger)
    leadership: Mapped[int] = mapped_column(SmallInteger)
    loyalty: Mapped[int] = mapped_column(SmallInteger)
    form: Mapped[int] = mapped_column(SmallInteger)
    experience: Mapped[int] = mapped_column(Integer, default=0)
    morale: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))
    retired_after_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))
    injured_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Staff(Base):
    __tablename__ = "staff"
    __table_args__ = (
        CheckConstraint(f"role IS NULL OR role IN ({STAFF_ROLES_SQL})", name="ck_staff_role"),
        Index(
            "uq_staff_club_role",
            "club_id",
            "role",
            unique=True,
            postgresql_where=text("club_id IS NOT NULL"),
        ),
        CheckConstraint("(club_id IS NULL) = (role IS NULL)", name="ck_staff_employment"),
        CheckConstraint("status IN ('active', 'last_season', 'retired')", name="ck_staff_status"),
        CheckConstraint(
            "(status = 'active' AND last_season_id IS NULL AND retired_after_season_id IS NULL) OR "
            "(status = 'last_season' AND last_season_id IS NOT NULL AND "
            "retired_after_season_id IS NULL) OR (status = 'retired' AND "
            "last_season_id IS NOT NULL AND retired_after_season_id IS NOT NULL AND "
            "retired_after_season_id = last_season_id "
            "AND club_id IS NULL)",
            name="ck_staff_retirement",
        ),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)
    club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), index=True)
    role: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")
    last_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))
    retired_after_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))


class StaffQualification(Base):
    __tablename__ = "staff_qualifications"
    __table_args__ = (
        CheckConstraint(f"role IN ({STAFF_ROLES_SQL})", name="ck_qualification_role"),
        CheckConstraint("skill BETWEEN 1 AND 20", name="ck_qualification_skill"),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("staff.person_id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    skill: Mapped[int] = mapped_column(SmallInteger)
    quality_tier: Mapped[int] = mapped_column(
        SmallInteger, Computed("((skill - 1) / 4) + 1", persisted=True)
    )
    policy_version: Mapped[str] = mapped_column(String(40), server_default="staff-quality-v1")
    qualified_for_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))


class CandidatePool(Base):
    """Global supply counter, serialized with appearance/market allocation."""

    __tablename__ = "candidate_pools"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'player' AND profile = 'player' AND target = 12) OR "
            f"(kind = 'staff' AND profile IN ({STAFF_ROLES_SQL}) AND target = 2)",
            name="ck_candidate_pool_scope",
        ),
        CheckConstraint("generation >= 0", name="ck_candidate_generation"),
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    profile: Mapped[str] = mapped_column(String(32), primary_key=True)
    home_league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"))
    target: Mapped[int] = mapped_column(SmallInteger)
    generation: Mapped[int] = mapped_column(Integer, default=0)


class CandidateOffer(Base):
    __tablename__ = "candidate_offers"
    __table_args__ = (
        CheckConstraint(
            "economy_policy IN ('economy-cycle-v1','economy-cycle-v2')",
            name="ck_candidate_offers_economy",
        ),
        ForeignKeyConstraint(
            ["kind", "pool_profile"],
            ["candidate_pools.kind", "candidate_pools.profile"],
            name="fk_candidate_pool",
        ),
        CheckConstraint(
            "status IN ('available','private','signed','retired')", name="ck_candidate_status"
        ),
        CheckConstraint("fee > 0 AND weekly_rate > 0", name="ck_candidate_price"),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    pool_profile: Mapped[str] = mapped_column(String(32))
    profile: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="available")
    fee: Mapped[int] = mapped_column(BigInteger)
    economy_policy: Mapped[str] = mapped_column(String(32), server_default="economy-cycle-v1")
    weekly_rate: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CandidateSigning(Base):
    __tablename__ = "candidate_signings"
    __table_args__ = (
        CheckConstraint(
            "economy_policy IN ('economy-cycle-v1','economy-cycle-v2')",
            name="ck_candidate_signings_economy",
        ),
        CheckConstraint("fee > 0 AND weekly_rate > 0", name="ck_signing_price"),
        CheckConstraint("kind IN ('player','staff')", name="ck_signing_kind"),
        Index(
            "uq_candidate_player_signing",
            "person_id",
            unique=True,
            postgresql_where=text("kind = 'player'"),
        ),
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("candidate_offers.person_id"))
    kind: Mapped[str] = mapped_column(String(16))
    profile: Mapped[str] = mapped_column(String(32))
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    fee: Mapped[int] = mapped_column(BigInteger)
    economy_policy: Mapped[str] = mapped_column(String(32), server_default="economy-cycle-v1")
    weekly_rate: Mapped[int] = mapped_column(Integer)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StaffChange(Base):
    """Durable confirmation pins both employees and all quoted terms."""

    __tablename__ = "staff_changes"
    __table_args__ = (
        CheckConstraint(
            "incoming_id IS NOT NULL OR outgoing_id IS NOT NULL", name="ck_staff_change_people"
        ),
        CheckConstraint(
            "incoming_id IS NULL OR outgoing_id IS NULL OR incoming_id != outgoing_id",
            name="ck_staff_change_distinct",
        ),
        CheckConstraint(f"role IN ({STAFF_ROLES_SQL})", name="ck_staff_change_role"),
        CheckConstraint(
            "(executed_at IS NULL) = (execution_operation_id IS NULL)",
            name="ck_staff_change_execution",
        ),
        UniqueConstraint("execution_operation_id", name="uq_staff_change_execution"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    role: Mapped[str] = mapped_column(String(32))
    outgoing_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff.person_id"))
    incoming_id: Mapped[UUID | None] = mapped_column(ForeignKey("staff.person_id"))
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_operation_id: Mapped[UUID | None] = mapped_column(Uuid)


class PersonSeasonEvent(Base):
    """Persisted once-only aging, retirement draws and career transition outcomes."""

    __tablename__ = "person_season_events"
    __table_args__ = (
        UniqueConstraint("person_id", "season_id", "stage", name="uq_person_season_stage"),
        CheckConstraint(
            "stage IN ('age_applied','player_last_decision','player_retired',"
            "'staff_last_decision','staff_retired','career_assigned','youth_replacement')",
            name="ck_person_event_stage",
        ),
        CheckConstraint("age BETWEEN 0 AND 150", name="ck_person_event_age"),
        CheckConstraint(
            "probability_bps IS NULL OR probability_bps BETWEEN 0 AND 10000",
            name="ck_person_event_probability",
        ),
        CheckConstraint("draw IS NULL OR draw BETWEEN 0 AND 9999", name="ck_person_event_draw"),
        CheckConstraint(
            "stage NOT IN ('player_last_decision','staff_last_decision') OR "
            "(is_last_season IS NOT NULL AND probability_bps IS NOT NULL)",
            name="ck_person_event_decision",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), index=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    stage: Mapped[str] = mapped_column(String(32))
    age: Mapped[int] = mapped_column(SmallInteger)
    policy_version: Mapped[str] = mapped_column(String(80))
    probability_bps: Mapped[int | None] = mapped_column(SmallInteger)
    draw: Mapped[int | None] = mapped_column(SmallInteger)
    is_last_season: Mapped[bool | None] = mapped_column(Boolean)
    related_person_id: Mapped[UUID | None] = mapped_column(ForeignKey("persons.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClubLineup(Base):
    __tablename__ = "club_lineups"
    __table_args__ = (
        CheckConstraint("version > 0 AND tactics_version > 0", name="ck_lineup_versions"),
    )
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"), primary_key=True)
    formation: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    tactics_version: Mapped[int] = mapped_column(Integer)
    tactics: Mapped[dict[str, Any]] = mapped_column(JSONB)


class LineupSlot(Base):
    __tablename__ = "lineup_slots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["person_id", "club_id"],
            ["players.person_id", "players.club_id"],
            name="fk_lineup_player_club",
        ),
        UniqueConstraint("club_id", "person_id", name="uq_lineup_player"),
        CheckConstraint("slot BETWEEN 1 AND 20", name="ck_lineup_slot"),
        CheckConstraint(f"position IN ({POSITIONS_SQL})", name="ck_lineup_position"),
    )
    club_id: Mapped[UUID] = mapped_column(ForeignKey("club_lineups.club_id"), primary_key=True)
    slot: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    person_id: Mapped[UUID] = mapped_column(Uuid)
    position: Mapped[str] = mapped_column(String(3))


class WorldAdmission(Base):
    """New unowned set stays private until its chronological bot replay catches up."""

    __tablename__ = "world_admissions"
    __table_args__ = (
        UniqueConstraint("league_set_id", name="uq_admission_set"),
        CheckConstraint("replay_from <= requested_at", name="ck_admission_replay"),
        CheckConstraint("ready_at IS NULL OR ready_at >= requested_at", name="ck_admission_ready"),
    )
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"))
    reference_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))
    replay_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season_id", "home_club_id", "league_id"],
            ["season_clubs.season_id", "season_clubs.club_id", "season_clubs.league_id"],
            name="fk_fixture_home_membership",
        ),
        ForeignKeyConstraint(
            ["season_id", "away_club_id", "league_id"],
            ["season_clubs.season_id", "season_clubs.club_id", "season_clubs.league_id"],
            name="fk_fixture_away_membership",
        ),
        ForeignKeyConstraint(
            ["season_id", "home_club_id"],
            ["season_contestants.season_id", "season_contestants.club_id"],
            name="fk_fixture_home_season_club",
        ),
        ForeignKeyConstraint(
            ["season_id", "away_club_id"],
            ["season_contestants.season_id", "season_contestants.club_id"],
            name="fk_fixture_away_season_club",
        ),
        CheckConstraint("home_club_id <> away_club_id", name="ck_fixture_distinct_clubs"),
        CheckConstraint("round_number BETWEEN 1 AND 18", name="ck_fixture_round"),
        CheckConstraint("lineup_lock_at <= scheduled_start_at", name="ck_fixture_lock"),
        CheckConstraint(
            "status IN ('scheduled','live','paused','finished','abandoned','cancelled')",
            name="ck_fixture_status",
        ),
        CheckConstraint(
            "competition IN ('league','cup','champions','friendly')",
            name="ck_fixture_competition",
        ),
        CheckConstraint(
            "competition <> 'league' OR league_id IS NOT NULL",
            name="ck_fixture_league",
        ),
        CheckConstraint(
            "(status IN ('finished','abandoned')) = (actual_finish_at IS NOT NULL)",
            name="ck_fixture_finish",
        ),
        CheckConstraint(
            "status NOT IN ('live','paused','finished','abandoned') OR actual_start_at IS NOT NULL",
            name="ck_fixture_started",
        ),
        CheckConstraint(
            "actual_finish_at IS NULL OR (actual_start_at IS NOT NULL AND "
            "actual_finish_at >= actual_start_at)",
            name="ck_fixture_actual_dates",
        ),
        Index(
            "uq_fixture_league_pair",
            "season_id",
            "home_club_id",
            "away_club_id",
            unique=True,
            postgresql_where=text("competition = 'league'"),
        ),
        Index(
            "uq_fixture_competition_leg",
            "season_id",
            "competition",
            "stage",
            text("COALESCE(leg, 0)"),
            "home_club_id",
            "away_club_id",
            unique=True,
            postgresql_where=text("competition <> 'league'"),
        ),
        Index("ix_fixture_schedule", "status", "scheduled_start_at"),
        Index("ix_fixture_league_round", "season_id", "league_id", "round_number"),
        Index("ix_fixture_home_history", "home_club_id", "status", "scheduled_start_at", "id"),
        Index("ix_fixture_away_history", "away_club_id", "status", "scheduled_start_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    season_id: Mapped[UUID] = mapped_column(Uuid)
    league_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    competition: Mapped[str] = mapped_column(String(16), server_default="league")
    stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    leg: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    neutral: Mapped[bool] = mapped_column(Boolean, server_default="false")
    home_club_id: Mapped[UUID] = mapped_column(Uuid)
    away_club_id: Mapped[UUID] = mapped_column(Uuid)
    round_number: Mapped[int] = mapped_column(SmallInteger)
    scheduled_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lineup_lock_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="scheduled")


class FixturePlan(Base):
    """Season structure only. No kickoff or match jobs until announcement."""

    __tablename__ = "fixture_plans"
    __table_args__ = (
        CheckConstraint(
            "competition IN ('league','cup','champions','friendly')", name="ck_plan_competition"
        ),
        CheckConstraint("revision > 0", name="ck_plan_revision"),
        CheckConstraint(
            "home_club_id IS NULL OR away_club_id IS NULL OR home_club_id <> away_club_id",
            name="ck_plan_distinct",
        ),
        CheckConstraint(
            "(home_club_id IS NOT NULL AND away_club_id IS NOT NULL) "
            "OR conditional_ref IS NOT NULL",
            name="ck_plan_participants",
        ),
        Index("ix_plan_day", "assigned_day", "announced_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    league_id: Mapped[UUID | None] = mapped_column(ForeignKey("leagues.id"))
    competition: Mapped[str] = mapped_column(String(16), server_default="league")
    round_number: Mapped[int] = mapped_column(SmallInteger)
    home_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"))
    away_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"))
    conditional_ref: Mapped[str | None] = mapped_column(String(200))
    possible_club_ids: Mapped[list[str]] = mapped_column(JSONB, server_default="[]")
    stage: Mapped[str | None] = mapped_column(String(80))
    leg: Mapped[int | None] = mapped_column(SmallInteger)
    neutral: Mapped[bool] = mapped_column(Boolean, server_default="false")
    assigned_day: Mapped[date] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    planning_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(String(40))


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("id", "fixture_id", name="uq_match_fixture_identity"),
        CheckConstraint("completed_tick BETWEEN 0 AND 40", name="ck_match_tick"),
        CheckConstraint("home_goals >= 0 AND away_goals >= 0", name="ck_match_score"),
        CheckConstraint("state_version > 0", name="ck_match_state_version"),
        CheckConstraint(
            "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused',"
            "'extra_break','extra_first_half','extra_half_time','extra_second_half','penalties')",
            name="ck_match_phase",
        ),
        CheckConstraint(
            "phase <> 'full_time' OR completed_tick IN (30, 40)", name="ck_match_full_time"
        ),
        Index(
            "ix_match_snapshot_gin",
            "starting_snapshot",
            postgresql_using="gin",
            postgresql_ops={"starting_snapshot": "jsonb_path_ops"},
        ),
        CheckConstraint(
            "paused_seconds >= 0 AND (phase = 'paused') = (paused_at IS NOT NULL)",
            name="ck_match_pause",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    fixture_id: Mapped[UUID] = mapped_column(ForeignKey("fixtures.id"), unique=True)
    seed: Mapped[int] = mapped_column(BigInteger)
    engine_version: Mapped[str] = mapped_column(String(80))
    state_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    starting_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    completed_tick: Mapped[int] = mapped_column(SmallInteger, default=0)
    phase: Mapped[str] = mapped_column(String(32), default="first_half")
    home_goals: Mapped[int] = mapped_column(SmallInteger, default=0)
    away_goals: Mapped[int] = mapped_column(SmallInteger, default=0)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_seconds: Mapped[int] = mapped_column(Integer, server_default="0")


class MatchRecovery(Base):
    __tablename__ = "match_recoveries"
    __table_args__ = (
        UniqueConstraint("match_id", "previous_due_at", name="uq_recovery_window"),
        CheckConstraint("checkpoint_tick BETWEEN 0 AND 40", name="ck_recovery_tick"),
        CheckConstraint(
            "next_due_at > previous_due_at AND next_due_at > recovered_at", name="ck_recovery_due"
        ),
        CheckConstraint("reason IN ('worker_lag','late_resume')", name="ck_recovery_reason"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"))
    checkpoint_tick: Mapped[int] = mapped_column(SmallInteger)
    state_hash: Mapped[str] = mapped_column(String(64))
    previous_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(24))


class MatchTick(Base):
    __tablename__ = "match_ticks"
    __table_args__ = (
        CheckConstraint("tick_number BETWEEN 1 AND 40", name="ck_tick_number"),
        CheckConstraint(
            "game_minute BETWEEN tick_number * 3 - 2 AND tick_number * 3",
            name="ck_tick_game_minute",
        ),
    )
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    tick_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    game_minute: Mapped[int] = mapped_column(SmallInteger)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    state_hash: Mapped[str] = mapped_column(String(64))


class AcceptedCommand(Base):
    __tablename__ = "accepted_commands"
    __table_args__ = (
        Index("ix_accepted_command_time", "accepted_at"),
        UniqueConstraint("telegram_update_id", "effect_number", name="uq_command_update_effect"),
        ForeignKeyConstraint(
            ["match_id", "applied_tick_number"],
            ["match_ticks.match_id", "match_ticks.tick_number"],
            name="fk_command_applied_tick",
        ),
        ForeignKeyConstraint(
            ["ownership_id", "manager_id", "club_id"],
            ["ownership_periods.id", "ownership_periods.manager_id", "ownership_periods.club_id"],
            name="fk_command_owner_club",
        ),
        CheckConstraint("effect_number >= 0", name="ck_command_effect"),
        CheckConstraint("payload_version > 0", name="ck_command_payload_version"),
        CheckConstraint("status IN ('accepted','applied','cancelled')", name="ck_command_status"),
        CheckConstraint(
            "kind IN ('lineup','tactics','substitution','instruction','mentality')",
            name="ck_command_kind",
        ),
        CheckConstraint(
            "kind = 'lineup' OR match_id IS NOT NULL", name="ck_command_match_required"
        ),
        CheckConstraint(
            "target_tick_number IS NULL OR target_tick_number BETWEEN 1 AND 40",
            name="ck_command_target_tick",
        ),
        CheckConstraint(
            "applied_tick_number IS NULL OR (status = 'applied' AND applied_at IS NOT NULL)",
            name="ck_command_applied_tick",
        ),
        CheckConstraint(
            "(status = 'applied') = (applied_at IS NOT NULL)", name="ck_command_applied_at"
        ),
        Index("ix_command_match_pending", "match_id", "status", "target_tick_number"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    operation_key: Mapped[str] = mapped_column(String(200), unique=True)
    telegram_update_id: Mapped[int] = mapped_column(BigInteger)
    effect_number: Mapped[int] = mapped_column(SmallInteger)
    manager_id: Mapped[UUID] = mapped_column(ForeignKey("managers.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    match_id: Mapped[UUID | None] = mapped_column(ForeignKey("matches.id"))
    kind: Mapped[str] = mapped_column(String(16))
    payload_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="accepted")
    target_tick_number: Mapped[int | None] = mapped_column(SmallInteger)
    applied_tick_number: Mapped[int | None] = mapped_column(SmallInteger)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Standing(Base):
    __tablename__ = "standings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season_id", "club_id"],
            ["season_clubs.season_id", "season_clubs.club_id"],
            name="fk_standing_membership",
        ),
        CheckConstraint("played = won + drawn + lost", name="ck_standing_played"),
        CheckConstraint("points = won * 3 + drawn", name="ck_standing_points"),
        CheckConstraint(
            "played BETWEEN 0 AND 18 AND won >= 0 AND drawn >= 0 AND lost >= 0 "
            "AND goals_for >= 0 AND goals_against >= 0",
            name="ck_standing_counters",
        ),
    )
    season_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    played: Mapped[int] = mapped_column(SmallInteger, default=0)
    won: Mapped[int] = mapped_column(SmallInteger, default=0)
    drawn: Mapped[int] = mapped_column(SmallInteger, default=0)
    lost: Mapped[int] = mapped_column(SmallInteger, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    points: Mapped[int] = mapped_column(SmallInteger, default=0)


class WorldSeedReceipt(Base):
    """Committed seed key, independent of subsequent gameplay changes."""

    __tablename__ = "world_seed_receipts"
    __table_args__ = (CheckConstraint("length(config_hash) = 64", name="ck_world_seed_hash"),)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"), unique=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"), unique=True)
    config_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AnalyticsCoverage(Base):
    __tablename__ = "analytics_coverage"
    __table_args__ = (
        CheckConstraint("version = 1", name="ck_coverage_version"),
        CheckConstraint("retention_days BETWEEN 1 AND 730", name="ck_coverage_retention"),
    )
    version: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retention_days: Mapped[int] = mapped_column(Integer)


class AnalyticsExclusion(Base):
    """Explicit internal/test/operator registry and deletion tombstones. Restricted."""

    __tablename__ = "analytics_exclusions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('internal','test','operator','opt_out')", name="ck_exclusion_kind"
        ),
    )
    manager_id: Mapped[UUID] = mapped_column(ForeignKey("managers.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    excluded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JourneySession(Base):
    __tablename__ = "journey_sessions"
    __table_args__ = (
        CheckConstraint("last_action_at >= first_action_at", name="ck_session_time"),
        UniqueConstraint("id", "analytics_manager_id", name="uq_session_manager"),
        Index("ix_session_manager_time", "analytics_manager_id", "last_action_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    analytics_manager_id: Mapped[UUID] = mapped_column(
        ForeignKey("analytics_managers.id", ondelete="CASCADE")
    )
    first_action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_action_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


JOURNEY_NAMES = (
    "friendly_opened",
    "friendly_scheduled",
    "friendly_invited",
    "friendly_responded",
    "academy_candidate_signed",
    "academy_opened",
    "academy_upgraded",
    "action_rejected",
    "bot_unavailable",
    "bot_youth_replacement_created",
    "campaign_touched",
    "candidate_signed",
    "club_claimed",
    "club_offer_delivered",
    "club_opened",
    "club_ownership_ended",
    "club_preview_opened",
    "experiment_exposed",
    "feedback_answered",
    "feedback_prompt_delivered",
    "feedback_skipped",
    "finances_opened",
    "fixture_abandoned",
    "fixture_finished",
    "fixture_started",
    "help_delivered",
    "lineup_opened",
    "lineup_saved",
    "manager_started",
    "market_bid_accepted",
    "market_listing_cancelled",
    "market_listing_created",
    "market_opened",
    "match_command_accepted",
    "match_command_applied",
    "match_lineup_locked",
    "match_opened",
    "match_report_opened",
    "match_second_half_started",
    "match_tick_committed",
    "next_action_opened",
    "next_fixture_opened",
    "player_last_season_announced",
    "player_retired",
    "result_delivered",
    "result_finalized",
    "stadium_expansion_ordered",
    "stadium_opened",
    "start_accepted",
    "training_opened",
    "training_plan_saved",
    "scout_search_started",
    "scout_search_cancelled",
)


class JourneyEventRow(Base):
    __tablename__ = "journey_events"
    __table_args__ = (
        CheckConstraint(
            "name IN (" + ",".join(f"'{n}'" for n in JOURNEY_NAMES) + ")", name="ck_journey_name"
        ),
        UniqueConstraint("version", "operation_id", "effect_number", name="uq_journey_effect"),
        ForeignKeyConstraint(
            ["session_id", "analytics_manager_id"],
            ["journey_sessions.id", "journey_sessions.analytics_manager_id"],
            ondelete="CASCADE",
            name="fk_journey_session_manager",
        ),
        CheckConstraint(
            "version = 1 AND effect_number BETWEEN 0 AND 32767", name="ck_journey_version"
        ),
        CheckConstraint("recorded_at >= occurred_at", name="ck_journey_time"),
        CheckConstraint(
            "origin IN ('user','system','automation','operator')", name="ck_journey_origin"
        ),
        CheckConstraint(
            "(origin = 'user' OR session_id IS NULL) AND "
            "(NOT user_activity OR session_id IS NOT NULL)",
            name="ck_journey_session_origin",
        ),
        CheckConstraint("NOT user_activity OR origin = 'user'", name="ck_journey_activity"),
        Index("ix_journey_manager_time", "analytics_manager_id", "occurred_at"),
        Index("ix_journey_retention", "recorded_at"),
        Index(
            "uq_journey_manager_start",
            "analytics_manager_id",
            unique=True,
            postgresql_where="name = 'manager_started'",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    version: Mapped[int] = mapped_column(SmallInteger)
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    effect_number: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str] = mapped_column(String(80))
    origin: Mapped[str] = mapped_column(String(16))
    analytics_manager_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("analytics_managers.id", ondelete="CASCADE")
    )
    session_id: Mapped[UUID | None] = mapped_column(Uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_activity: Mapped[bool] = mapped_column(Boolean)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSONB)


class MatchStage(Base):
    __tablename__ = "match_stages"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('locked','started','second_half','finished','abandoned','extra_first',"
            "'extra_second') OR stage ~ '^penalty:[1-9][0-9]*$'",
            name="ck_match_stage",
        ),
    )
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(80), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    state_hash: Mapped[str] = mapped_column(String(64))


class OperationAttempt(Base):
    __tablename__ = "operation_attempts"
    __table_args__ = (
        Index("ix_operation_attempt_started", "started_at"),
        UniqueConstraint("job_id", "retry_number", name="uq_attempt_job_retry"),
        CheckConstraint("retry_number > 0 AND finished_at >= started_at", name="ck_attempt_time"),
        CheckConstraint("outcome IN ('committed','deferred','failed')", name="ck_attempt_outcome"),
        CheckConstraint(
            "failure_class IS NULL OR failure_class IN "
            "('not_ready','invalid_state','unsupported_job_kind')",
            name="ck_attempt_failure",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"))
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    fixture_id: Mapped[UUID | None] = mapped_column(ForeignKey("fixtures.id"))
    retry_number: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(16))
    failure_class: Mapped[str | None] = mapped_column(String(32))


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["season_id", "home_club_id", "league_id"],
            ["season_clubs.season_id", "season_clubs.club_id", "season_clubs.league_id"],
            name="fk_result_home_membership",
        ),
        ForeignKeyConstraint(
            ["season_id", "away_club_id", "league_id"],
            ["season_clubs.season_id", "season_clubs.club_id", "season_clubs.league_id"],
            name="fk_result_away_membership",
        ),
        UniqueConstraint("fixture_id", name="uq_result_fixture"),
        ForeignKeyConstraint(
            ["match_id", "fixture_id"],
            ["matches.id", "matches.fixture_id"],
            name="fk_result_match_fixture",
        ),
        ForeignKeyConstraint(
            ["season_id", "home_club_id"],
            ["season_contestants.season_id", "season_contestants.club_id"],
            name="fk_result_home_season_club",
        ),
        ForeignKeyConstraint(
            ["season_id", "away_club_id"],
            ["season_contestants.season_id", "season_contestants.club_id"],
            name="fk_result_away_season_club",
        ),
        CheckConstraint(
            "version = 1 AND home_goals >= 0 AND away_goals >= 0 AND home_club_id <> away_club_id",
            name="ck_result_score",
        ),
        CheckConstraint(
            "competition IN ('league','cup','champions','friendly')",
            name="ck_result_competition",
        ),
        CheckConstraint(
            "competition <> 'league' OR league_id IS NOT NULL",
            name="ck_result_league",
        ),
        CheckConstraint(
            "actual_finish_at >= actual_start_at AND recorded_at >= actual_finish_at",
            name="ck_result_dates",
        ),
        CheckConstraint("length(state_hash) = 64", name="ck_result_hash"),
    )
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    fixture_id: Mapped[UUID] = mapped_column(ForeignKey("fixtures.id"))
    season_id: Mapped[UUID] = mapped_column(Uuid)
    league_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    competition: Mapped[str] = mapped_column(String(16), server_default="league")
    home_club_id: Mapped[UUID] = mapped_column(Uuid)
    away_club_id: Mapped[UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(SmallInteger)
    home_goals: Mapped[int] = mapped_column(SmallInteger)
    away_goals: Mapped[int] = mapped_column(SmallInteger)
    actual_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_finish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state_hash: Mapped[str] = mapped_column(String(64))


class AcquisitionCampaign(Base):
    __tablename__ = "acquisition_campaigns"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Za-z0-9_-]{1,64}$'", name="ck_campaign_code"),
        CheckConstraint(
            "source IN ('telegram_channel','telegram_ads','community','referral','other')",
            name="ck_campaign_source",
        ),
        CheckConstraint("placement ~ '^[a-z0-9_-]{1,80}$'", name="ck_campaign_placement"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(24))
    placement: Mapped[str] = mapped_column(String(80))
    published_on: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AcquisitionFirstTouch(Base):
    __tablename__ = "acquisition_first_touches"
    __table_args__ = (
        CheckConstraint("kind IN ('direct','unknown','known')", name="ck_first_touch_kind"),
        CheckConstraint(
            "(kind = 'known') = (campaign_id IS NOT NULL)", name="ck_first_touch_campaign"
        ),
        Index("ix_first_touch_time", "started_at"),
    )
    analytics_manager_id: Mapped[UUID] = mapped_column(
        ForeignKey("analytics_managers.id", ondelete="CASCADE"), primary_key=True
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("acquisition_campaigns.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AcquisitionTouch(Base):
    __tablename__ = "acquisition_touches"
    __table_args__ = (
        CheckConstraint("kind IN ('direct','unknown','known')", name="ck_touch_kind"),
        CheckConstraint("(kind = 'known') = (campaign_id IS NOT NULL)", name="ck_touch_campaign"),
        CheckConstraint("recorded_at >= occurred_at", name="ck_touch_time"),
        Index("ix_touch_retention", "recorded_at"),
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    analytics_manager_id: Mapped[UUID] = mapped_column(
        ForeignKey("analytics_managers.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(16))
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("acquisition_campaigns.id"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class GameActionReceipt(Base):
    """Operational dedupe independent of optional/expiring analytics."""

    __tablename__ = "game_action_receipts"
    __table_args__ = (
        Index("ix_action_manager_time", "manager_id", "occurred_at"),
        ForeignKeyConstraint(
            ["ownership_id", "manager_id", "club_id"],
            ["ownership_periods.id", "ownership_periods.manager_id", "ownership_periods.club_id"],
            name="fk_action_ownership",
        ),
        CheckConstraint("effect_number BETWEEN 0 AND 32767", name="ck_action_effect"),
        CheckConstraint("ownership_id IS NULL OR club_id IS NOT NULL", name="ck_action_club"),
        CheckConstraint(
            "kind IN ('club_claimed','club_opened','club_preview_opened','lineup_opened',"
            "'lineup_saved','match_command_accepted','match_opened','match_report_opened',"
            "'next_fixture_opened','notification_preferences',"
            "'training_opened','training_plan_saved','language_preferences','finances_opened','stadium_opened','stadium_expansion_ordered','market_opened',"
            "'market_listing_created','market_bid_accepted','market_listing_cancelled','candidate_signed',"
            "'scout_search_started','scout_search_cancelled','academy_opened','academy_upgraded',"
            "'academy_candidate_signed','friendly_opened','friendly_scheduled','friendly_invited',"
            "'friendly_responded','entry_opened')",
            name="ck_action_kind",
        ),
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    effect_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    manager_id: Mapped[UUID] = mapped_column(ForeignKey("managers.id"))
    ownership_id: Mapped[UUID | None] = mapped_column(Uuid)
    club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"))
    kind: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    fixture_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("fixtures.id", name="fk_action_fixture")
    )


class TelegramPortraitFile(Base):
    __tablename__ = "telegram_portrait_files"
    __table_args__ = (
        CheckConstraint("length(cache_key) = 64", name="ck_portrait_file_key"),
        CheckConstraint("catalog_version > 0", name="ck_portrait_file_catalog"),
        CheckConstraint("kit_version > 0", name="ck_portrait_file_kit_version"),
        CheckConstraint("size IN (96, 256)", name="ck_portrait_file_size"),
        CheckConstraint("length(file_id) BETWEEN 1 AND 500", name="ck_portrait_file_id"),
    )
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(100))
    variant_id: Mapped[str] = mapped_column(String(100))
    catalog_version: Mapped[int] = mapped_column(Integer)
    kit_primary: Mapped[str] = mapped_column(String(7))
    kit_secondary: Mapped[str] = mapped_column(String(7))
    kit_version: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer)
    background: Mapped[str] = mapped_column(
        String(32), default="transparent", server_default="transparent"
    )
    file_id: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlayerDevelopment(Base):
    __tablename__ = "player_development"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(caps) = 'object' AND jsonb_typeof(progress) = 'object'",
            name="ck_development_objects",
        ),
        CheckConstraint("policy_version = 'training-v1'", name="ck_development_policy"),
        CheckConstraint(
            "growth_age BETWEEN 0 AND 150 AND leadership_progress BETWEEN 0 AND 9999",
            name="ck_development_season",
        ),
        CheckConstraint("jsonb_typeof(season_gains) = 'object'", name="ck_development_gains"),
        CheckConstraint(
            "growth_policy = 'development-budget-v1'", name="ck_development_growth_policy"
        ),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)
    growth_policy: Mapped[str] = mapped_column(String(32), server_default="development-budget-v1")
    growth_age: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    season_gains: Mapped[dict[str, int]] = mapped_column(JSONB, server_default="{}")
    leadership_progress: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    caps: Mapped[dict[str, int]] = mapped_column(JSONB)
    progress: Mapped[dict[str, int]] = mapped_column(JSONB)
    policy_version: Mapped[str] = mapped_column(String(32))


class AcademyCandidate(Base):
    """Durable, season-scoped academy shortlist; person rows appear only on signing."""

    __tablename__ = "academy_candidates"
    __table_args__ = (
        UniqueConstraint("club_id", "season_id", "slot", name="uq_academy_club_season_slot"),
        CheckConstraint("policy_version = 'academy-v1'", name="ck_academy_policy"),
        CheckConstraint("slot >= 1", name="ck_academy_slot"),
        CheckConstraint("age BETWEEN 17 AND 18", name="ck_academy_age"),
        CheckConstraint(f"position IN ({POSITIONS_SQL})", name="ck_academy_position"),
        CheckConstraint(
            f"cardinality(alternative_positions) <= 2 AND "
            f"alternative_positions <@ ARRAY[{POSITIONS_SQL}]::varchar[] AND "
            "NOT position = ANY(alternative_positions) AND "
            "array_position(alternative_positions, NULL) IS NULL AND "
            "(cardinality(alternative_positions) < 2 OR "
            "alternative_positions[1] <> alternative_positions[2])",
            name="ck_academy_alternatives",
        ),
        CheckConstraint(
            "jsonb_typeof(skills) = 'object' AND jsonb_typeof(caps) = 'object'",
            name="ck_academy_profiles",
        ),
        CheckConstraint("status IN ('available','signed','expired')", name="ck_academy_status"),
        CheckConstraint("(status = 'signed') = (signed_at IS NOT NULL)", name="ck_academy_signed"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"), index=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    slot: Mapped[int] = mapped_column(SmallInteger)
    policy_version: Mapped[str] = mapped_column(String(32), server_default="academy-v1")
    name: Mapped[str] = mapped_column(String(120))
    age: Mapped[int] = mapped_column(SmallInteger)
    position: Mapped[str] = mapped_column(String(3))
    alternative_positions: Mapped[list[str]] = mapped_column(ARRAY(String(3)), default=list)
    skills: Mapped[dict[str, int]] = mapped_column(JSONB)
    caps: Mapped[dict[str, int]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="available")
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrainingPlan(Base):
    __tablename__ = "training_plans"
    __table_args__ = (
        UniqueConstraint("club_id", "week_start", name="uq_training_club_week"),
        CheckConstraint(
            "focus IN ('goalkeeping','defending','playmaking','winger','attacking',"
            "'set_pieces','physical','team')",
            name="ck_training_focus",
        ),
        CheckConstraint("intensity IN ('low','medium','high')", name="ck_training_intensity"),
        CheckConstraint("status IN ('scheduled','applied','cancelled')", name="ck_training_status"),
        CheckConstraint(
            "(status = 'applied') = (applied_at IS NOT NULL)", name="ck_training_applied"
        ),
        CheckConstraint(
            "due_at = week_start + interval '1 day' "
            "AND selected_at >= week_start - interval '9 days' "
            "AND selected_at < due_at",
            name="ck_training_week",
        ),
        CheckConstraint(
            "focus <> 'set_pieces' OR executor_id IS NOT NULL", name="ck_training_executor"
        ),
        CheckConstraint(
            "policy_version IN ('training-daily-v1','training-specialists-v2')",
            name="ck_training_policy",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    week_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    focus: Mapped[str] = mapped_column(String(20))
    intensity: Mapped[str] = mapped_column(String(16))
    executor_id: Mapped[UUID | None] = mapped_column(ForeignKey("persons.id"))
    status: Mapped[str] = mapped_column(String(16))
    policy_version: Mapped[str] = mapped_column(String(32))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    staff_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class TrainingDecision(Base):
    __tablename__ = "training_decisions"
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("training_plans.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class FinanceAccount(Base):
    __tablename__ = "finance_accounts"
    __table_args__ = (
        CheckConstraint(
            "reserved >= 0 AND arrears >= 0 AND sequence >= 1", name="ck_finance_account"
        ),
    )
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"), primary_key=True)
    reserved: Mapped[int] = mapped_column(BigInteger, server_default="0")
    arrears: Mapped[int] = mapped_column(BigInteger, server_default="0")
    sequence: Mapped[int] = mapped_column(Integer, server_default="1")


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("club_id", "operation_key", name="uq_ledger_operation"),
        UniqueConstraint("club_id", "sequence", name="uq_ledger_sequence"),
        CheckConstraint("sequence >= 1", name="ck_ledger_sequence"),
        CheckConstraint(
            "balance_after >= 0 AND reserved_after >= 0 AND "
            "reserved_after <= balance_after AND arrears_after >= 0",
            name="ck_ledger_balances",
        ),
        CheckConstraint(
            "cash_delta <> 0 OR reserved_delta <> 0 OR arrears_delta <> 0 OR kind = 'opening'",
            name="ck_ledger_effect",
        ),
        CheckConstraint(
            "kind IN ('opening','grant','tickets','sponsor','merchandise',"
            "'player_salary','staff_salary','stadium_upkeep','prize','reserve','release',"
            "'transfer_in','transfer_out','arrears_paid','stadium_upgrade','signing_fee',"
            "'scout_search','academy_upkeep','academy_upgrade')",
            name="ck_ledger_kind",
        ),
        Index("ix_ledger_club_page", "club_id", "sequence"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    operation_key: Mapped[str] = mapped_column(String(180))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    cash_delta: Mapped[int] = mapped_column(BigInteger)
    reserved_delta: Mapped[int] = mapped_column(BigInteger)
    arrears_delta: Mapped[int] = mapped_column(BigInteger)
    balance_after: Mapped[int] = mapped_column(BigInteger)
    reserved_after: Mapped[int] = mapped_column(BigInteger)
    arrears_after: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)


class MoneyReservation(Base):
    __tablename__ = "money_reservations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_reservation_amount"),
        CheckConstraint("status IN ('active','released','settled')", name="ck_reservation_status"),
        CheckConstraint("(status = 'active') = (closed_at IS NULL)", name="ck_reservation_closed"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    purpose: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SalaryContract(Base):
    __tablename__ = "salary_contracts"
    __table_args__ = (
        CheckConstraint("recovery_skill BETWEEN 0 AND 20", name="ck_salary_recovery_skill"),
        CheckConstraint("weekly_rate >= 0", name="ck_salary_rate"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_salary_dates"),
        CheckConstraint("role IN ('player'," + STAFF_ROLES_SQL + ")", name="ck_salary_role"),
        Index(
            "uq_salary_active_person", "person_id", unique=True, postgresql_where="ended_at IS NULL"
        ),
        Index("ix_salary_club_period", "club_id", "started_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"))
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    role: Mapped[str] = mapped_column(String(32))
    weekly_rate: Mapped[int] = mapped_column(BigInteger)
    policy_version: Mapped[str] = mapped_column(String(32), server_default="economy-cycle-v1")
    recovery_skill: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinanceCycle(Base):
    __tablename__ = "finance_cycles"
    __table_args__ = (
        UniqueConstraint("club_id", "week_start", name="uq_finance_week"),
        CheckConstraint(
            "policy_version IN ('economy-cycle-v1','economy-cycle-v2')", name="ck_finance_policy"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    week_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    policy_version: Mapped[str] = mapped_column(String(32))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)


class StadiumExpansion(Base):
    __tablename__ = "stadium_expansions"
    __table_args__ = (
        CheckConstraint(
            "from_capacity IN (5000,10000,20000,35000) AND "
            "to_capacity IN (10000,20000,35000,50000) AND "
            "to_capacity > from_capacity AND cost > 0",
            name="ck_expansion_dimensions",
        ),
        CheckConstraint(
            "due_at > started_at AND (completed_at IS NULL OR completed_at >= due_at)",
            name="ck_expansion_dates",
        ),
        CheckConstraint(
            "status IN ('queued','finished') AND "
            "(status = 'finished') = (completed_at IS NOT NULL)",
            name="ck_expansion_state",
        ),
        UniqueConstraint("operation_id", name="uq_expansion_operation"),
        Index(
            "uq_expansion_pending_club",
            "club_id",
            unique=True,
            postgresql_where="status = 'queued'",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    from_capacity: Mapped[int] = mapped_column(Integer)
    to_capacity: Mapped[int] = mapped_column(Integer)
    cost: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))


class MatchCrowd(Base):
    __tablename__ = "match_crowds"
    __table_args__ = (
        CheckConstraint(
            "policy_version IN ('stadium-demand-v1','neutral-final-v1')", name="ck_crowd_policy"
        ),
    )
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(32))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        CheckConstraint(
            "economy_policy IN ('economy-cycle-v1','economy-cycle-v2')", name="ck_auctions_economy"
        ),
        UniqueConstraint("operation_id", name="uq_auction_operation"),
        CheckConstraint("policy_version = 'auction-v1'", name="ck_auction_policy"),
        CheckConstraint(
            "initial_price >= 100000 AND weekly_rate BETWEEN 1 AND 200000", name="ck_auction_money"
        ),
        CheckConstraint(
            "player_age BETWEEN 17 AND 38 AND ends_at >= created_at + interval '48 hours'",
            name="ck_auction_dates",
        ),
        CheckConstraint(
            "status IN ('open','sold','expired','cancelled') AND "
            "(status = 'open') = (closed_at IS NULL)",
            name="ck_auction_status",
        ),
        CheckConstraint(
            "(status = 'open') = (close_reason IS NULL)", name="ck_auction_reason_state"
        ),
        CheckConstraint(
            "close_reason IS NULL OR close_reason IN ('sold','no_bids','last_season','retired',"
            "'ownership_changed','roster_changed','calendar_changed','withdrawn','operator',"
            "'funds_changed')",
            name="ck_auction_reason",
        ),
        Index(
            "uq_auction_open_player", "person_id", unique=True, postgresql_where="status = 'open'"
        ),
        ForeignKeyConstraint(
            ["id", "highest_bid_id"],
            ["auction_bids.auction_id", "auction_bids.id"],
            name="fk_auction_highest_bid",
            use_alter=True,
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("players.person_id"))
    seller_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    seller_ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"))
    policy_version: Mapped[str] = mapped_column(String(32), server_default="auction-v1")
    player_age: Mapped[int] = mapped_column(SmallInteger)
    initial_price: Mapped[int] = mapped_column(BigInteger)
    economy_policy: Mapped[str] = mapped_column(String(32), server_default="economy-cycle-v1")
    weekly_rate: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    highest_bid_id: Mapped[UUID | None] = mapped_column(Uuid)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String(32))


class AuctionBid(Base):
    __tablename__ = "auction_bids"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_bid_operation"),
        UniqueConstraint("auction_id", "id", name="uq_bid_auction_identity"),
        UniqueConstraint("reservation_id", name="uq_bid_reservation"),
        CheckConstraint("amount >= 100000", name="ck_bid_amount"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    auction_id: Mapped[UUID] = mapped_column(ForeignKey("auctions.id"))
    buyer_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    buyer_ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    reservation_id: Mapped[UUID] = mapped_column(ForeignKey("money_reservations.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TransferReceipt(Base):
    __tablename__ = "transfer_receipts"
    __table_args__ = (
        UniqueConstraint("auction_id", name="uq_transfer_auction"),
        CheckConstraint("price >= 100000", name="ck_transfer_price"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    auction_id: Mapped[UUID] = mapped_column(ForeignKey("auctions.id"))
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"))
    seller_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    buyer_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    price: Mapped[int] = mapped_column(BigInteger)
    transferred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)


FEEDBACK_REASON_CODES = (
    "unclear_next_step",
    "difficult_lineup",
    "long_wait",
    "inconvenient_time",
    "uninteresting_game",
    "technical_problem",
    "other",
)


class FeedbackCampaign(Base):
    """Voluntary feedback campaign registry. Append-only; no game rules."""

    __tablename__ = "feedback_campaigns"
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9_-]{1,64}$'", name="ck_feedback_campaign_slug"),
        UniqueConstraint("slug", name="uq_feedback_campaign_slug"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FeedbackResponse(Base):
    """Durable voluntary-feedback session. One row per manager/campaign.

    Free text lives in feedback_comments only; this table stores bounded codes.
    Operational dedupe (operation_id) and cooldown (prompted_at) are independent
    of journey retention/opt-out deletion.
    """

    __tablename__ = "feedback_responses"
    __table_args__ = (
        CheckConstraint(
            "trigger_kind IS NULL OR (trigger_kind IN ('entry_help','missed_match') "
            "AND expires_at IS NOT NULL AND expires_at > prompted_at AND "
            "(trigger_kind <> 'missed_match' OR (ownership_id IS NOT NULL "
            "AND fixture_id IS NOT NULL)))",
            name="ck_feedback_trigger",
        ),
        UniqueConstraint("manager_id", "campaign_id", name="uq_feedback_manager_campaign"),
        UniqueConstraint("operation_id", name="uq_feedback_operation"),
        CheckConstraint(
            "status IN ('prompted','answered','skipped','opted_out')",
            name="ck_feedback_status",
        ),
        CheckConstraint(
            "reason IS NULL OR reason IN "
            "('unclear_next_step','difficult_lineup','long_wait','inconvenient_time',"
            "'uninteresting_game','technical_problem','other')",
            name="ck_feedback_reason",
        ),
        CheckConstraint(
            "(status = 'prompted') = (responded_at IS NULL)",
            name="ck_feedback_responded",
        ),
        CheckConstraint(
            "(status = 'answered') = (reason IS NOT NULL AND responded_at IS NOT NULL)",
            name="ck_feedback_answer",
        ),
        CheckConstraint(
            "responded_at IS NULL OR responded_at >= prompted_at",
            name="ck_feedback_response_time",
        ),
        CheckConstraint(
            "comment_due_at IS NULL OR comment_due_at >= responded_at",
            name="ck_feedback_comment_due",
        ),
        Index("ix_feedback_manager_prompted", "manager_id", "prompted_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("feedback_campaigns.id"))
    manager_id: Mapped[UUID] = mapped_column(ForeignKey("managers.id"))
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32))
    prompted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trigger_kind: Mapped[str | None] = mapped_column(String(24))
    ownership_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ownership_periods.id", name="fk_feedback_ownership")
    )
    fixture_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("fixtures.id", name="fk_feedback_fixture")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeedbackComment(Base):
    """Restricted free-text storage. Never mirrored into journey envelopes."""

    __tablename__ = "feedback_comments"
    __table_args__ = (
        CheckConstraint(
            "char_length(text) BETWEEN 1 AND 500",
            name="ck_feedback_comment_length",
        ),
        Index("ix_feedback_comment_created", "created_at"),
    )
    response_id: Mapped[UUID] = mapped_column(
        ForeignKey("feedback_responses.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(String(500))


class PlayerRecovery(Base):
    __tablename__ = "player_recovery"
    __table_args__ = (
        CheckConstraint("energy_bps BETWEEN 0 AND 10000", name="ck_recovery_energy"),
        CheckConstraint("remainder BETWEEN 0 AND 86399", name="ck_recovery_remainder"),
        CheckConstraint(
            "stamina BETWEEN 1 AND 20 AND age BETWEEN 17 AND 38", name="ck_recovery_traits"
        ),
        CheckConstraint("policy_version = 'recovery-v1'", name="ck_recovery_policy"),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("players.person_id"), primary_key=True)
    energy_bps: Mapped[int] = mapped_column(Integer, default=10000)
    remainder: Mapped[int] = mapped_column(Integer, default=0)
    recovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stamina: Mapped[int] = mapped_column(SmallInteger)
    age: Mapped[int] = mapped_column(SmallInteger)
    active_match_id: Mapped[UUID | None] = mapped_column(ForeignKey("matches.id"))
    policy_version: Mapped[str] = mapped_column(String(32), default="recovery-v1")


class ScoutMission(Base):
    __tablename__ = "scout_missions"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_scout_start_operation"),
        CheckConstraint(f"position IN ({POSITIONS_SQL})", name="ck_scout_position"),
        CheckConstraint(
            "skill BETWEEN 1 AND 20 AND days IN (4,8,12,16) AND fee > 0", name="ck_scout_terms"
        ),
        CheckConstraint(
            "policy_version = 'scouting-v1' AND economy_policy = 'economy-cycle-v2'",
            name="ck_scout_policy",
        ),
        CheckConstraint("due_at = started_at + days * interval '1 day'", name="ck_scout_duration"),
        CheckConstraint(
            "status IN ('active','completed','cancelled','expired') "
            "AND (status = 'active') = (finished_at IS NULL)",
            name="ck_scout_state",
        ),
        CheckConstraint("finished_at IS NULL OR finished_at >= started_at", name="ck_scout_finish"),
        CheckConstraint(
            "(status IN ('completed','expired')) = (expires_at IS NOT NULL)", name="ck_scout_expiry"
        ),
        Index("uq_scout_active_club", "club_id", unique=True, postgresql_where="status = 'active'"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    scout_id: Mapped[UUID] = mapped_column(ForeignKey("staff.person_id"))
    skill: Mapped[int] = mapped_column(SmallInteger)
    position: Mapped[str] = mapped_column(String(4))
    days: Mapped[int] = mapped_column(SmallInteger)
    fee: Mapped[int] = mapped_column(BigInteger)
    policy_version: Mapped[str] = mapped_column(String(32))
    economy_policy: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))


class ScoutDiscovery(Base):
    __tablename__ = "scout_discoveries"
    __table_args__ = (
        UniqueConstraint("mission_id", "rank", name="uq_scout_rank"),
        CheckConstraint(
            "potential_low BETWEEN 1 AND 20 AND potential_high BETWEEN potential_low AND 20 "
            "AND NOT (signed_at IS NOT NULL AND declined_at IS NOT NULL)",
            name="ck_scout_potential",
        ),
        CheckConstraint(
            "rank BETWEEN 1 AND 4 AND fee > 0 AND cycle_rate > 0", name="ck_scout_discovery"
        ),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("players.person_id"), primary_key=True)
    mission_id: Mapped[UUID] = mapped_column(ForeignKey("scout_missions.id"), index=True)
    rank: Mapped[int] = mapped_column(SmallInteger)
    fee: Mapped[int] = mapped_column(BigInteger)
    cycle_rate: Mapped[int] = mapped_column(BigInteger)
    potential_low: Mapped[int] = mapped_column(SmallInteger)
    potential_high: Mapped[int] = mapped_column(SmallInteger)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    declined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ScoutSigning(Base):
    __tablename__ = "scout_signings"
    __table_args__ = (
        UniqueConstraint("person_id", name="uq_scout_person_signing"),
        CheckConstraint("fee > 0 AND cycle_rate > 0", name="ck_scout_signing_money"),
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    person_id: Mapped[UUID] = mapped_column(ForeignKey("scout_discoveries.person_id"))
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    fee: Mapped[int] = mapped_column(BigInteger)
    cycle_rate: Mapped[int] = mapped_column(BigInteger)
    economy_policy: Mapped[str] = mapped_column(String(32))
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MatchDevelopment(Base):
    __tablename__ = "match_development"
    __table_args__ = (
        CheckConstraint(
            "policy_version IN ('match-development-v1','match-practice-v2')",
            name="ck_match_development_policy",
        ),
        CheckConstraint(
            "jsonb_typeof(staff_snapshot) = 'object' AND jsonb_typeof(results) = 'array'",
            name="ck_match_development_documents",
        ),
        CheckConstraint(
            "jsonb_typeof(terms_snapshot) = 'object' AND "
            "(policy_version = 'match-development-v1' OR ("
            "terms_snapshot ?& ARRAY['competition','fixture_id','season_id','plan_revision',"
            "'match_seed','friendly_skill_factor','friendly_experience_factor','players'] AND "
            "terms_snapshot->>'competition' IN ('league','cup','champions','friendly') AND "
            "jsonb_typeof(terms_snapshot->'players') = 'object' AND "
            "jsonb_typeof(terms_snapshot->'match_seed') = 'string' AND "
            "length(terms_snapshot->>'match_seed') > 0 AND "
            "(terms_snapshot->>'friendly_skill_factor')::int BETWEEN 1 AND 9999 AND "
            "(terms_snapshot->>'friendly_experience_factor')::int BETWEEN 1 AND 9999) IS TRUE)",
            name="ck_match_development_terms",
        ),
    )
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(32))
    eligible: Mapped[bool] = mapped_column(Boolean)
    staff_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    terms_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class StaffCareer(Base):
    __tablename__ = "staff_careers"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({STAFF_ROLES_SQL}) AND skill BETWEEN 1 AND 20",
            name="ck_staff_career_qualification",
        ),
        CheckConstraint("policy_version = 'retired-staff-v1'", name="ck_staff_career_policy"),
        CheckConstraint("status IN ('pending','public','accepted')", name="ck_staff_career_status"),
        CheckConstraint(
            "expires_at > created_at AND ((status = 'pending') = (resolved_at IS NULL))",
            name="ck_staff_career_resolution",
        ),
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("staff.person_id"), primary_key=True)
    origin_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"))
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    role: Mapped[str] = mapped_column(String(32))
    skill: Mapped[int] = mapped_column(SmallInteger)
    policy_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SponsorContract(Base):
    __tablename__ = "sponsor_contracts"
    __table_args__ = (
        UniqueConstraint("club_id", "season_id", name="uq_sponsor_club_season"),
        CheckConstraint("policy_version = 'season-sponsor-v1'", name="ck_sponsor_policy"),
        CheckConstraint(
            "basis IN ('opening','transition','season_result')", name="ck_sponsor_basis"
        ),
        CheckConstraint(
            "tier BETWEEN 1 AND 5 AND (place IS NULL OR place BETWEEN 1 AND 10) "
            "AND supporters >= 0 AND cycle_rate > 0",
            name="ck_sponsor_terms",
        ),
        CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ck_sponsor_dates"),
        Index(
            "uq_sponsor_active_club",
            "club_id",
            unique=True,
            postgresql_where=text("ends_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    source_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))
    basis: Mapped[str] = mapped_column(String(24))
    policy_version: Mapped[str] = mapped_column(String(32))
    tier: Mapped[int] = mapped_column(SmallInteger)
    place: Mapped[int | None] = mapped_column(SmallInteger)
    supporters: Mapped[int] = mapped_column(Integer)
    cycle_rate: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CupBracket(Base):
    __tablename__ = "cup_brackets"
    __table_args__ = (
        CheckConstraint("seed >= 0", name="ck_cup_bracket_seed"),
        CheckConstraint(
            "policy_version = 'cup-bracket-v1' AND jsonb_typeof(entry_snapshot) = 'array' "
            "AND jsonb_array_length(entry_snapshot) = 5",
            name="ck_cup_bracket_policy",
        ),
    )
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"))
    seed: Mapped[int] = mapped_column(BigInteger)
    policy_version: Mapped[str] = mapped_column(String(40), server_default="cup-bracket-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


class CupTie(Base):
    __tablename__ = "cup_ties"
    __table_args__ = (
        CheckConstraint(
            "(home_kind = 'club' AND home_club_id IS NOT NULL AND home_tie_id IS NULL) "
            "OR (home_kind = 'winner' AND home_tie_id IS NOT NULL)",
            name="ck_cup_tie_home_slot",
        ),
        CheckConstraint(
            "(away_kind = 'club' AND away_club_id IS NOT NULL AND away_tie_id IS NULL) "
            "OR (away_kind = 'winner' AND away_tie_id IS NOT NULL)",
            name="ck_cup_tie_away_slot",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'decided')",
            name="ck_cup_tie_status",
        ),
        CheckConstraint(
            "(status = 'decided') = (winner_club_id IS NOT NULL) AND "
            "(status = 'decided') = (decided_at IS NOT NULL) AND "
            "(winner_club_id IS NULL OR (home_club_id IS NOT NULL AND away_club_id IS NOT NULL "
            "AND winner_club_id IN (home_club_id,away_club_id)))",
            name="ck_cup_tie_winner",
        ),
        Index("ix_cup_ties_season", "season_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    round_key: Mapped[str] = mapped_column(String(40))
    round_label: Mapped[str] = mapped_column(String(80))
    home_kind: Mapped[str] = mapped_column(String(16))
    home_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), nullable=True)
    home_tie_id: Mapped[UUID | None] = mapped_column(ForeignKey("cup_ties.id"), nullable=True)
    away_kind: Mapped[str] = mapped_column(String(16))
    away_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), nullable=True)
    away_tie_id: Mapped[UUID | None] = mapped_column(ForeignKey("cup_ties.id"), nullable=True)
    cup_days: Mapped[list[int]] = mapped_column(JSONB)
    neutral: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    winner_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), nullable=True)
    first_match_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    second_match_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    extra_time_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    shootout_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NeutralFinalBooking(Base):
    __tablename__ = "neutral_final_bookings"
    __table_args__ = (
        UniqueConstraint("venue_club_id", "assigned_day", name="uq_neutral_venue_day"),
        CheckConstraint("jsonb_typeof(basis) = 'object'", name="ck_neutral_booking_basis"),
    )
    fixture_id: Mapped[UUID] = mapped_column(ForeignKey("fixtures.id"), primary_key=True)
    venue_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    venue_name: Mapped[str] = mapped_column(String(80))
    assigned_day: Mapped[date] = mapped_column(Date)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB)


class SeasonAllocationReceipt(Base):
    """Frozen cross-set promotion/relegation draw for one rollover cohort."""

    __tablename__ = "season_allocation_receipts"
    __table_args__ = (
        UniqueConstraint("cohort_key", name="uq_allocation_cohort"),
        CheckConstraint("policy_version = 'league-allocation-v1'", name="ck_allocation_policy"),
        CheckConstraint("seed >= 0", name="ck_allocation_seed"),
        CheckConstraint("jsonb_typeof(season_ids) = 'array'", name="ck_allocation_seasons"),
        CheckConstraint("jsonb_typeof(assignments) = 'array'", name="ck_allocation_assignments"),
        CheckConstraint("jsonb_typeof(draws) = 'array'", name="ck_allocation_draws"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    cohort_key: Mapped[str] = mapped_column(String(128))
    seed: Mapped[int] = mapped_column(BigInteger)
    policy_version: Mapped[str] = mapped_column(String(40))
    season_ids: Mapped[list[str]] = mapped_column(JSONB)
    assignments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    draws: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChampionsDraw(Base):
    """Frozen qualifying cohort for one playing season (single set, AND-126)."""

    __tablename__ = "champions_draws"
    __table_args__ = (
        UniqueConstraint("season_id", name="uq_champions_draw_season"),
        CheckConstraint("policy_version = 'champions-groups-v1'", name="ck_champions_draw_policy"),
        CheckConstraint("seed >= 0", name="ck_champions_draw_seed"),
        CheckConstraint(
            "jsonb_typeof(snapshots) = 'array' AND jsonb_array_length(snapshots) >= 5 "
            "AND mod(jsonb_array_length(snapshots),5) = 0",
            name="ck_champions_draw_snapshots",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    qualifying_season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    league_set_id: Mapped[UUID] = mapped_column(ForeignKey("league_sets.id"))
    cohort_key: Mapped[str] = mapped_column(String(128))
    seed: Mapped[int] = mapped_column(BigInteger)
    policy_version: Mapped[str] = mapped_column(String(40))
    snapshots: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChampionsTournament(Base):
    """One per-tier champions cup of a playing season (AND-126)."""

    __tablename__ = "champions_tournaments"
    __table_args__ = (
        UniqueConstraint("season_id", "tier", "group_index", name="uq_champions_tournament"),
        CheckConstraint("tier BETWEEN 1 AND 5", name="ck_champions_tournament_tier"),
        CheckConstraint("group_index >= 0", name="ck_champions_tournament_group"),
        CheckConstraint(
            "participant_count IN (8,16,32,64,128,256,512)",
            name="ck_champions_tournament_size",
        ),
        CheckConstraint(
            "policy_version = 'champions-bracket-v1'", name="ck_champions_tournament_policy"
        ),
        CheckConstraint("seed >= 0 AND bracket_seed >= 0", name="ck_champions_tournament_seed"),
        Index("ix_champions_tournaments_season", "season_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    qualifying_season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    tier: Mapped[int] = mapped_column(Integer)
    group_index: Mapped[int] = mapped_column(Integer)
    participant_count: Mapped[int] = mapped_column(Integer)
    cohort_key: Mapped[str] = mapped_column(String(128))
    seed: Mapped[int] = mapped_column(BigInteger)
    bracket_seed: Mapped[int] = mapped_column(BigInteger)
    policy_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChampionsTie(Base):
    """One knockout tie of a champions tournament (AND-126)."""

    __tablename__ = "champions_ties"
    __table_args__ = (
        CheckConstraint(
            "(home_kind = 'club' AND home_club_id IS NOT NULL AND home_tie_id IS NULL) "
            "OR (home_kind = 'winner' AND home_tie_id IS NOT NULL)",
            name="ck_champions_tie_home_slot",
        ),
        CheckConstraint(
            "(away_kind = 'club' AND away_club_id IS NOT NULL AND away_tie_id IS NULL) "
            "OR (away_kind = 'winner' AND away_tie_id IS NOT NULL)",
            name="ck_champions_tie_away_slot",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'decided')",
            name="ck_champions_tie_status",
        ),
        CheckConstraint(
            "(status = 'decided') = (winner_club_id IS NOT NULL) AND "
            "(status = 'decided') = (decided_at IS NOT NULL) AND "
            "(winner_club_id IS NULL OR (home_club_id IS NOT NULL AND away_club_id IS NOT NULL "
            "AND winner_club_id IN (home_club_id,away_club_id)))",
            name="ck_champions_tie_winner",
        ),
        Index("ix_champions_ties_season", "season_id"),
        Index("ix_champions_ties_tournament", "tournament_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tournament_id: Mapped[UUID] = mapped_column(ForeignKey("champions_tournaments.id"))
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    round_number: Mapped[int] = mapped_column(SmallInteger)
    tie_index: Mapped[int] = mapped_column(SmallInteger)
    round_label: Mapped[str] = mapped_column(String(40))
    home_kind: Mapped[str] = mapped_column(String(16))
    home_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), nullable=True)
    home_tie_id: Mapped[UUID | None] = mapped_column(ForeignKey("champions_ties.id"), nullable=True)
    away_kind: Mapped[str] = mapped_column(String(16))
    away_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), nullable=True)
    away_tie_id: Mapped[UUID | None] = mapped_column(ForeignKey("champions_ties.id"), nullable=True)
    champions_cycles: Mapped[list[int]] = mapped_column(JSONB)
    neutral: Mapped[bool] = mapped_column(Boolean, server_default="false")
    status: Mapped[str] = mapped_column(String(20), server_default="pending")
    winner_club_id: Mapped[UUID | None] = mapped_column(ForeignKey("clubs.id"), nullable=True)
    first_match_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    second_match_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    extra_time_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    shootout_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FriendlyInvitation(Base):
    """Explicit human challenge for a voluntary friendly; no auto pairing."""

    __tablename__ = "friendly_invitations"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_friendly_invite_plan"),
        CheckConstraint("home_club_id <> away_club_id", name="ck_friendly_invite_clubs"),
        CheckConstraint(
            "status IN ('pending','accepted','declined','cancelled','expired')",
            name="ck_friendly_invite_status",
        ),
        CheckConstraint(
            "(status = 'accepted') = (plan_id IS NOT NULL)",
            name="ck_friendly_invite_plan",
        ),
        CheckConstraint(
            "(status = 'pending') = (responded_at IS NULL)",
            name="ck_friendly_invite_response",
        ),
        Index("ix_friendly_invite_day", "season_id", "assigned_day"),
        Index("ix_friendly_invite_clubs", "home_club_id", "away_club_id", "assigned_day"),
        Index(
            "uq_friendly_pending_pair_day",
            "season_id",
            "home_club_id",
            "away_club_id",
            "assigned_day",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"))
    home_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    away_club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"))
    challenger_ownership_id: Mapped[UUID] = mapped_column(ForeignKey("ownership_periods.id"))
    challenged_ownership_id: Mapped[UUID | None] = mapped_column(ForeignKey("ownership_periods.id"))
    assigned_day: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan_id: Mapped[UUID | None] = mapped_column(ForeignKey("fixture_plans.id"), nullable=True)


class SeasonContestant(Base):
    """Fixture eligibility: local league members and frozen champions guests."""

    __tablename__ = "season_contestants"
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"), primary_key=True)


class AcademyIntake(Base):
    """Frozen academy terms, unaffected by later upgrades or owner changes."""

    __tablename__ = "academy_intakes"
    __table_args__ = (CheckConstraint("level BETWEEN 1 AND 5", name="ck_academy_intake_level"),)
    club_id: Mapped[UUID] = mapped_column(ForeignKey("clubs.id"), primary_key=True)
    season_id: Mapped[UUID] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    level: Mapped[int] = mapped_column(SmallInteger)
