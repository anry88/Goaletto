from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from goaletto.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(), pool_pre_ping=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions.begin() as session:
            yield session

    @asynccontextmanager
    async def read_connection(self) -> AsyncIterator[AsyncConnection]:
        """One consistent PostgreSQL snapshot; writes rejected by the server."""
        async with self.engine.connect() as connection:
            connection = await connection.execution_options(
                isolation_level="REPEATABLE READ", postgresql_readonly=True
            )
            async with connection.begin():
                yield connection

    @asynccontextmanager
    async def read_transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.read_connection() as connection:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                yield session

    async def close(self) -> None:
        await self.engine.dispose()
