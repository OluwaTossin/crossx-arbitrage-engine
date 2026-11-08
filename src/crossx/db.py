"""Append-only observations. Alembic owns DDL; decimal JSON values are strings."""

from sqlalchemy import BigInteger, Column, DateTime, Identity, MetaData, String, Table, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine

from crossx.schemas import Opportunity, OrderBook

metadata = MetaData()
books = Table(
    "book_observations",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("exchange", String(16), nullable=False),
    Column("symbol", String(16), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False, index=True),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("payload", JSONB, nullable=False),
)
opportunities = Table(
    "opportunities",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("symbol", String(16), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False, index=True),
    Column("payload", JSONB, nullable=False),
)


class Database:
    def __init__(self, database_url: str) -> None:
        self.engine = create_async_engine(database_url, pool_pre_ping=True)

    async def initialize(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def save_book(self, book: OrderBook) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                books.insert().values(
                    exchange=book.exchange,
                    symbol=book.symbol,
                    observed_at=book.observed_at,
                    received_at=book.received_at,
                    payload=book.model_dump(mode="json"),
                )
            )

    async def save_opportunity(self, opportunity: Opportunity) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                opportunities.insert().values(
                    symbol=opportunity.symbol,
                    observed_at=opportunity.observed_at,
                    payload=opportunity.model_dump(mode="json"),
                )
            )

    async def close(self) -> None:
        await self.engine.dispose()
