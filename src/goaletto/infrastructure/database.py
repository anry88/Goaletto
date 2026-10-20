from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from goaletto.config import Settings
from goaletto.infrastructure import economy_transaction  # noqa: F401
from goaletto.infrastructure.metrics import METRICS
from goaletto.infrastructure.metrics_sql import install


class Database:
    def __init__(self, settings: Settings) -> None:
        self.environment = settings.environment
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        install(self.engine.sync_engine)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        started, outcome = perf_counter(), "error"
        try:
            async with self.sessions.begin() as session:
                session.info["production"] = self.environment == "production"
                yield session
            outcome = "committed"
        finally:
            METRICS.transactions.labels("write", outcome).inc()
            METRICS.transaction_seconds.labels("write").observe(perf_counter() - started)

    @asynccontextmanager
    async def read_connection(self) -> AsyncIterator[AsyncConnection]:
        """One consistent PostgreSQL snapshot; writes rejected by the server."""
        started, outcome = perf_counter(), "error"
        try:
            async with self.engine.connect() as connection:
                connection = await connection.execution_options(
                    isolation_level="REPEATABLE READ", postgresql_readonly=True
                )
                async with connection.begin():
                    yield connection
            outcome = "completed"
        finally:
            METRICS.transactions.labels("read", outcome).inc()
            METRICS.transaction_seconds.labels("read").observe(perf_counter() - started)

    @asynccontextmanager
    async def read_transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.read_connection() as connection:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                yield session

    async def close(self) -> None:
        await self.engine.dispose()
