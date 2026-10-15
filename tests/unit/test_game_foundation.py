"""Offline checks only. These do not claim PostgreSQL execution or race coverage."""

import io
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from goaletto.infrastructure.models import Base
from goaletto.infrastructure.portrait_catalog import catalog_records


def normalized(sql: str) -> str:
    return " ".join(sql.split()).rstrip(";")


def test_frozen_migration_covers_orm_tables_and_indexes_without_connecting(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOALETTO_DATABASE_URL", "postgresql+asyncpg://offline.invalid/goaletto")
    output = io.StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = normalized(output.getvalue())
    old = {"league_sets", "leagues", "clubs", "jobs", "outbox_messages"}
    altered = {
        "fixtures",
        "matches",
        "match_ticks",
        "accepted_commands",
        "managers",
        "game_action_receipts",
        "journey_events",
        "persons",
        "ledger_entries",
        "candidate_signings",
        "staff_qualifications",
    }
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        if table.name not in old | altered:
            assert normalized(str(CreateTable(table).compile(dialect=dialect))) in sql
        for index in table.indexes:
            assert normalized(str(CreateIndex(index).compile(dialect=dialect))) in sql
    assert "CREATE TRIGGER tr_person_identity" in sql
    assert "CREATE TRIGGER tr_person_event_immutable" in sql
    assert "CREATE TRIGGER tr_match_tick_immutable" in sql
    assert "INSERT INTO persons" not in sql
    for table in altered | {"clubs"}:
        for constraint in Base.metadata.tables[table].constraints:
            if constraint.name and constraint.name in {
                "ck_fixture_status",
                "ck_fixture_finish",
                "ck_fixture_started",
                "ck_match_phase",
                "ck_match_pause",
                "ck_tick_game_minute",
                "ck_command_kind",
                "ck_club_crowd",
                "ck_action_kind",
                "ck_journey_name",
                "ck_signing_kind",
            }:
                assert (
                    normalized(
                        f"ALTER TABLE {table} ADD CONSTRAINT {constraint.name} "
                        f"CHECK ({constraint.sqltext})"
                    )
                    in sql
                )
    assert "ALTER TABLE clubs ADD COLUMN stadium_capacity INTEGER DEFAULT '5000' NOT NULL" in sql
    assert "ALTER TABLE matches ADD COLUMN paused_at TIMESTAMP WITH TIME ZONE" in sql
    assert "CREATE TRIGGER tr_match_stage_immutable" in sql
    assert (
        "ALTER TABLE managers ADD COLUMN reminders_enabled BOOLEAN DEFAULT 'true' NOT NULL" in sql
    )
    assert "ALTER TABLE managers ADD COLUMN bot_available BOOLEAN DEFAULT 'true' NOT NULL" in sql


def test_offline_downgrade_preserves_foundation_and_removes_extension(monkeypatch) -> None:
    monkeypatch.setenv("GOALETTO_DATABASE_URL", "postgresql+asyncpg://offline.invalid/goaletto")
    output = io.StringIO()
    config = Config("alembic.ini", output_buffer=output)
    command.downgrade(config, "0002_game_foundation:0001_foundation", sql=True)
    sql = output.getvalue()
    assert "DROP TABLE persons" in sql
    assert "DROP TABLE clubs" not in sql
    assert "DROP FUNCTION goaletto_guard_person_identity() CASCADE" in sql


def test_age_and_staff_variants_do_not_multiply_registered_people() -> None:
    identities, variants = catalog_records()
    assert len(identities) == 1080
    assert sum(r["origin_bank"] == "players" for r in identities) == 1000
    assert sum(r["origin_bank"] == "staff" for r in identities) == 80
    assert len(variants) >= len(identities)
    player_staff = next(r for r in variants if r["id"] == "p0001-staff-early-l1")
    assert player_staff["identity_id"] == "dugout-whole-v2/p0001"
    root = Path("assets/portraits/whole-v2")
    for variant in variants:
        assert (root / variant["asset_path"]).is_file()
        assert (root / variant["mask_path"]).is_file()
