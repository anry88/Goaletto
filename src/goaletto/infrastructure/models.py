"""Persistence for the game foundation; schema does not execute gameplay."""

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
    __table_args__ = (
        CheckConstraint("budget >= 0", name="ck_club_budget"),
        CheckConstraint("kit_primary ~ '^#[0-9A-Fa-f]{6}$'", name="ck_club_primary_color"),
        CheckConstraint("kit_secondary ~ '^#[0-9A-Fa-f]{6}$'", name="ck_club_secondary_color"),
        CheckConstraint("kit_version > 0", name="ck_club_kit_version"),
        CheckConstraint("stadium_capacity >= 0 AND supporters >= 0", name="ck_club_crowd"),
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
    network_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(24))


class Manager(Base):
    __tablename__ = "managers"
    __table_args__ = (CheckConstraint("telegram_id > 0", name="ck_manager_telegram_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    locale: Mapped[str] = mapped_column(String(16), server_default="ru")
    is_internal: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # NULL for legacy/imported managers; preview /start does not create a launch cohort.
    first_game_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    bot_available: Mapped[bool] = mapped_column(Boolean, server_default="true")
    bot_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    rollover_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
        ForeignKey("appearance_identities.id"), unique=True
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
    qualified_for_season_id: Mapped[UUID | None] = mapped_column(ForeignKey("seasons.id"))


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
        UniqueConstraint("season_id", "home_club_id", "away_club_id", name="uq_fixture_pair"),
        CheckConstraint("home_club_id <> away_club_id", name="ck_fixture_distinct_clubs"),
        CheckConstraint("round_number BETWEEN 1 AND 18", name="ck_fixture_round"),
        CheckConstraint("lineup_lock_at <= scheduled_start_at", name="ck_fixture_lock"),
        CheckConstraint(
            "status IN ('scheduled','live','paused','finished','abandoned','cancelled')",
            name="ck_fixture_status",
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
        Index("ix_fixture_schedule", "status", "scheduled_start_at"),
        Index("ix_fixture_league_round", "season_id", "league_id", "round_number"),
        Index("ix_fixture_home_history", "home_club_id", "status", "scheduled_start_at", "id"),
        Index("ix_fixture_away_history", "away_club_id", "status", "scheduled_start_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    season_id: Mapped[UUID] = mapped_column(Uuid)
    league_id: Mapped[UUID] = mapped_column(Uuid)
    home_club_id: Mapped[UUID] = mapped_column(Uuid)
    away_club_id: Mapped[UUID] = mapped_column(Uuid)
    round_number: Mapped[int] = mapped_column(SmallInteger)
    scheduled_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lineup_lock_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_finish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="scheduled")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("id", "fixture_id", name="uq_match_fixture_identity"),
        CheckConstraint("completed_tick BETWEEN 0 AND 30", name="ck_match_tick"),
        CheckConstraint("home_goals >= 0 AND away_goals >= 0", name="ck_match_score"),
        CheckConstraint("state_version > 0", name="ck_match_state_version"),
        CheckConstraint(
            "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused')",
            name="ck_match_phase",
        ),
        CheckConstraint("phase <> 'full_time' OR completed_tick = 30", name="ck_match_full_time"),
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
    phase: Mapped[str] = mapped_column(String(16), default="first_half")
    home_goals: Mapped[int] = mapped_column(SmallInteger, default=0)
    away_goals: Mapped[int] = mapped_column(SmallInteger, default=0)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_seconds: Mapped[int] = mapped_column(Integer, server_default="0")


class MatchRecovery(Base):
    __tablename__ = "match_recoveries"
    __table_args__ = (
        UniqueConstraint("match_id", "previous_due_at", name="uq_recovery_window"),
        CheckConstraint("checkpoint_tick BETWEEN 0 AND 29", name="ck_recovery_tick"),
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
        CheckConstraint("tick_number BETWEEN 1 AND 30", name="ck_tick_number"),
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
            "target_tick_number IS NULL OR target_tick_number BETWEEN 1 AND 30",
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
    "action_rejected",
    "bot_unavailable",
    "bot_youth_replacement_created",
    "campaign_touched",
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
            "stage IN ('locked','started','second_half','finished','abandoned')",
            name="ck_match_stage",
        ),
    )
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    stage: Mapped[str] = mapped_column(String(16), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    state_hash: Mapped[str] = mapped_column(String(64))


class OperationAttempt(Base):
    __tablename__ = "operation_attempts"
    __table_args__ = (
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
        UniqueConstraint("fixture_id", name="uq_result_fixture"),
        ForeignKeyConstraint(
            ["match_id", "fixture_id"],
            ["matches.id", "matches.fixture_id"],
            name="fk_result_match_fixture",
        ),
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
        CheckConstraint(
            "version = 1 AND home_goals >= 0 AND away_goals >= 0 AND home_club_id <> away_club_id",
            name="ck_result_score",
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
    league_id: Mapped[UUID] = mapped_column(Uuid)
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
            "'market_listing_created','market_bid_accepted','market_listing_cancelled')",
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
    )
    person_id: Mapped[UUID] = mapped_column(ForeignKey("persons.id"), primary_key=True)
    caps: Mapped[dict[str, int]] = mapped_column(JSONB)
    progress: Mapped[dict[str, int]] = mapped_column(JSONB)
    policy_version: Mapped[str] = mapped_column(String(32))


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
            "due_at = week_start + interval '7 days' AND selected_at >= week_start "
            "AND selected_at < due_at",
            name="ck_training_week",
        ),
        CheckConstraint(
            "focus <> 'set_pieces' OR executor_id IS NOT NULL", name="ck_training_executor"
        ),
        CheckConstraint("policy_version = 'training-v1'", name="ck_training_policy"),
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
            "'transfer_in','transfer_out','arrears_paid','stadium_upgrade')",
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
    policy_version: Mapped[str] = mapped_column(String(32), server_default="economy-v1")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FinanceCycle(Base):
    __tablename__ = "finance_cycles"
    __table_args__ = (
        UniqueConstraint("club_id", "week_start", name="uq_finance_week"),
        CheckConstraint("policy_version = 'economy-v1'", name="ck_finance_policy"),
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
        CheckConstraint("policy_version = 'stadium-demand-v1'", name="ck_crowd_policy"),
    )
    match_id: Mapped[UUID] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(32))
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB)


class Auction(Base):
    __tablename__ = "auctions"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_auction_operation"),
        CheckConstraint("policy_version = 'auction-v1'", name="ck_auction_policy"),
        CheckConstraint(
            "initial_price >= 100000 AND weekly_rate BETWEEN 180 AND 1700", name="ck_auction_money"
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
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comment_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
