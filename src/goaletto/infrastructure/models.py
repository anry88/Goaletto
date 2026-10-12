"""Persistence for the game foundation; schema does not execute gameplay."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
        CheckConstraint("completed_tick BETWEEN 0 AND 30", name="ck_match_tick"),
        CheckConstraint("home_goals >= 0 AND away_goals >= 0", name="ck_match_score"),
        CheckConstraint("state_version > 0", name="ck_match_state_version"),
        CheckConstraint(
            "phase IN ('first_half','half_time','second_half','full_time','abandoned','paused')",
            name="ck_match_phase",
        ),
        CheckConstraint("phase <> 'full_time' OR completed_tick = 30", name="ck_match_full_time"),
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


class JourneyEventRow(Base):
    __tablename__ = "journey_events"
    __table_args__ = (
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
