"""SQL recording only: no database, no connection and no claim of PG race coverage."""

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert

from goaletto.application.world import initialize_world
from goaletto.domain.world import WORLD_POLICY, WorldOptions, build_world
from goaletto.infrastructure.models import Club, WorldSeedReceipt

OPTIONS = WorldOptions(
    key="initial",
    seed=0,
    first_kickoff=datetime(2026, 10, 17, 18, tzinfo=UTC),
)
NOW = datetime(2026, 10, 12, tzinfo=UTC)


class EmptyValues:
    def all(self) -> list[str]:
        return []


class SQLRecorder:
    def __init__(self, receipt: WorldSeedReceipt | None = None) -> None:
        self.receipt = receipt
        self.writes: list[Insert] = []

    def in_transaction(self) -> bool:
        return True

    async def execute(self, statement, parameters=None):
        if isinstance(statement, Insert):
            self.writes.append(statement)

    async def get(self, model, key):
        if model is Club:
            return Club(id=key, supporters=2500)
        return self.receipt if model is WorldSeedReceipt else None

    async def flush(self):
        pass

    async def scalars(self, statement):
        return EmptyValues()


def receipt() -> WorldSeedReceipt:
    plan = build_world(OPTIONS)
    return WorldSeedReceipt(
        key=OPTIONS.key,
        league_set_id=plan.league_set_id,
        season_id=plan.season_id,
        config_hash=OPTIONS.fingerprint,
        policy_version=WORLD_POLICY,
        config={"naming_policy": OPTIONS.naming_policy},
        created_at=NOW,
    )


async def test_retry_after_matches_and_catalog_changes_does_not_reset_world(monkeypatch) -> None:
    monkeypatch.setattr(
        "goaletto.application.coaches.ensure_starting_coaches", AsyncMock(return_value=0)
    )
    session = SQLRecorder(receipt())

    def forbidden(*args, **kwargs):
        raise AssertionError("A completed seed must not reload or rebuild its world")

    monkeypatch.setattr("goaletto.application.world.prepare_world", forbidden)
    monkeypatch.setattr("goaletto.application.world.register_catalog", forbidden)
    result = await initialize_world(
        cast(AsyncSession, session),
        OPTIONS,
        initialized_at=NOW + timedelta(days=365),
    )
    assert not result.created
    assert session.writes == []


async def test_reusing_key_with_changed_configuration_is_rejected() -> None:
    session = SQLRecorder(receipt())
    changed = WorldOptions(
        key="initial",
        seed=1,
        first_kickoff=OPTIONS.first_kickoff,
    )
    with pytest.raises(ValueError, match="different seed"):
        await initialize_world(cast(AsyncSession, session), changed, initialized_at=NOW)
    assert session.writes == []


async def test_seed_batch_reserves_unique_people_and_keeps_all_jobs_held(monkeypatch) -> None:
    monkeypatch.setattr(
        "goaletto.application.coaches.ensure_starting_coaches", AsyncMock(return_value=50)
    )
    sponsors = AsyncMock()
    monkeypatch.setattr("goaletto.application.sponsorship.sign_sponsor", sponsors)
    session = SQLRecorder()

    async def registered(*args, **kwargs):
        return (1080, 2820)

    monkeypatch.setattr("goaletto.application.world.register_catalog", registered)
    result = await initialize_world(cast(AsyncSession, session), OPTIONS, initialized_at=NOW)
    assert result.created
    assert sponsors.await_count == 50
    assert session.writes[-1].table.name == "world_seed_receipts"
    people = []
    job_statuses = []
    for statement in session.writes:
        compiled = statement.compile(dialect=postgresql.dialect())
        assert len(compiled.params) < 32767  # asyncpg bind parameter limit.
        if statement.table.name == "persons":
            people.extend(
                value
                for key, value in compiled.params.items()
                if key.startswith("appearance_identity_id_m")
            )
        if statement.table.name == "jobs":
            job_statuses.extend(
                value
                for key, value in compiled.params.items()
                if key.startswith("status_m") or key == "status"
            )
        assert statement.table.name not in {"managers", "staff", "matches", "outbox_messages"}
    assert len(people) == len(set(people)) == 1000
    assert len(job_statuses) == 53 and set(job_statuses) == {"held"}


async def test_closed_preparation_window_is_rejected_before_any_population_write() -> None:
    session = SQLRecorder()
    with pytest.raises(ValueError, match="future kickoff"):
        await initialize_world(
            cast(AsyncSession, session), OPTIONS, initialized_at=OPTIONS.first_kickoff
        )
    assert session.writes == []
