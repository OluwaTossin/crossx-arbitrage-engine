"""Async migrations using the same environment configuration as the application."""

import asyncio

from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from crossx.config import Settings
from crossx.db import metadata


def run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async() -> None:
    engine = create_async_engine(Settings().database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(run_sync)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=Settings().database_url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async())
