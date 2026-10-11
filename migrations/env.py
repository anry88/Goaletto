import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from goaletto.config import Settings
from goaletto.infrastructure.models import Base

target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(
        url=Settings().database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def configure_and_run(connection):  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": Settings().database_url.get_secret_value()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(configure_and_run)
    await connectable.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
