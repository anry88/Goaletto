"""Execute the actual additive migration over existing people in the one local DB."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError

from goaletto.config import Settings
from tests.integration.test_candidates import funded

pytestmark = pytest.mark.integration


async def test_actual_migration_preserves_earned_values_and_audits_original_people(sessions):
    settings = Settings()
    target = make_url(settings.database_url.get_secret_value())
    assert settings.environment == "development"
    assert target.host in ("localhost", "127.0.0.1")
    assert target.port == 5433 and target.database == "goaletto"
    await funded(sessions)
    async with sessions.begin() as session:
        # The reset fixture already rejects production/protected data. Recreate
        # the pre-0063 shape inside this transaction, retaining the existing people.
        await session.execute(text("DROP TABLE position_experience_transitions"))
        for constraint in ("ck_player_experience_percent", "ck_player_experience_policy"):
            await session.execute(text(f"ALTER TABLE players DROP CONSTRAINT {constraint}"))
        await session.execute(text("ALTER TABLE players DROP COLUMN position_experience_policy"))
        await session.execute(text("DROP FUNCTION protect_position_experience_transition()"))
        await session.execute(text("DROP FUNCTION valid_percent_position_experience(jsonb)"))
        person_id = await session.scalar(
            text("SELECT person_id FROM players ORDER BY person_id LIMIT 1")
        )
        await session.execute(
            text("UPDATE players SET position_experience=CAST(:xp AS jsonb) WHERE person_id=:id"),
            {"xp": '{"DC":3024,"MC":10000,"FC":12345}', "id": person_id},
        )
        before = dict(
            (await session.execute(text("SELECT person_id,to_jsonb(p) FROM players p"))).all()
        )
        migration_path = (
            Path(__file__).parents[2] / "migrations/versions/0063_percent_position_experience.py"
        )
        spec = importlib.util.spec_from_file_location("percent_migration", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)

        def upgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

        await (await session.connection()).run_sync(upgrade)
        rows = (
            await session.execute(
                text(
                    "SELECT p.person_id,to_jsonb(p),t.before_values,t.after_values "
                    "FROM players p JOIN position_experience_transitions t "
                    "ON t.person_id=p.person_id"
                )
            )
        ).all()
        assert len(rows) == len(before) == 1000
        for pid, player, original, converted in rows:
            assert player.pop("position_experience_policy") == "position-experience-v2"
            player["position_experience"] = original
            assert player == before[pid]
            assert original == before[pid]["position_experience"]
            assert converted == (
                {"DC": 302400, "MC": 1000000, "FC": 1000000} if pid == person_id else {}
            )
        for statement in (
            "UPDATE position_experience_transitions SET before_values='{}'::jsonb "
            "WHERE person_id=:id",
            "DELETE FROM position_experience_transitions WHERE person_id=:id",
        ):
            with pytest.raises(DBAPIError, match="immutable"):
                async with session.begin_nested():
                    await session.execute(text(statement), {"id": person_id})
