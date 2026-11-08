"""Append-only observations. Alembic owns DDL; decimal JSON values are strings."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    String,
    Table,
    select,
    text,
)
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
Index("ix_book_observations_received_id", books.c.received_at, books.c.id)

opportunities = Table(
    "opportunities",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("symbol", String(16), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False, index=True),
    Column("payload", JSONB, nullable=False),
)


paper_sessions = Table(
    "paper_sessions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("config", JSONB, nullable=False),
    Column("provenance", JSONB, nullable=False),
    Column("result", JSONB, nullable=True),
)
paper_events = Table(
    "paper_events",
    metadata,
    Column("session_id", String(64), ForeignKey("paper_sessions.id"), primary_key=True),
    Column("sequence", BigInteger, primary_key=True),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("kind", String(32), nullable=False),
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

    async def save_books(self, observations: list[OrderBook]) -> None:
        if not observations:
            return
        async with self.engine.begin() as connection:
            await connection.execute(
                books.insert(),
                [
                    dict(
                        exchange=book.exchange,
                        symbol=book.symbol,
                        observed_at=book.observed_at,
                        received_at=book.received_at,
                        payload=book.model_dump(mode="json"),
                    )
                    for book in observations
                ],
            )

    async def storage_usage(self) -> tuple[int, int]:
        async with self.engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT pg_database_size(current_database()), "
                        "pg_total_relation_size('book_observations')"
                    )
                )
            ).one()
            return int(row[0]), int(row[1])

    async def retention_plan(self, days: int) -> int:
        # Planning only: no DELETE, expiry task or automatic cleanup exists.
        async with self.engine.connect() as connection:
            value = await connection.scalar(
                text(
                    "SELECT count(*) FROM book_observations "
                    "WHERE received_at < now() - make_interval(days => :days)"
                ),
                {"days": days},
            )
            return int(value or 0)

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

    async def iter_books(self, start: datetime, end: datetime) -> AsyncGenerator[OrderBook, None]:
        """Stable repeatable-read server cursor, receipt order with deterministic id tie-break."""
        async with self.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ")
            async with connection.begin():
                await connection.execute(text("SET TRANSACTION READ ONLY"))
                result = await connection.stream(
                    select(books.c.payload)
                    .where(books.c.received_at >= start, books.c.received_at < end)
                    .order_by(books.c.received_at, books.c.id)
                    .execution_options(yield_per=256)
                )
                try:
                    async for row in result:
                        yield OrderBook.model_validate(row[0])
                finally:
                    await result.close()

    async def start_session(
        self, session: str, config: dict[str, Any], provenance: dict[str, Any]
    ) -> None:
        async with self.engine.begin() as connection:
            await connection.execute(
                paper_sessions.insert().values(
                    id=session, created_at=datetime.now(UTC), config=config, provenance=provenance
                )
            )

    async def paper_checkpoint(
        self, session: str, events: list[dict[str, object]], result: dict[str, object] | None = None
    ) -> None:
        # Events and final balances/valuation commit together; no network calls in transaction.
        async with self.engine.begin() as connection:
            if events:
                await connection.execute(
                    paper_events.insert(),
                    [
                        {
                            "session_id": session,
                            "sequence": e["sequence"],
                            "at": datetime.fromisoformat(str(e["at"])),
                            "kind": e["kind"],
                            "payload": e,
                        }
                        for e in events
                    ],
                )
            if result is not None:
                await connection.execute(
                    paper_sessions.update()
                    .where(paper_sessions.c.id == session)
                    .values(result=result)
                )

    async def latest_paper_result(self) -> dict[str, Any] | None:
        async with self.engine.connect() as connection:
            result = await connection.scalar(
                select(paper_sessions.c.result)
                .where(paper_sessions.c.result.is_not(None))
                .order_by(paper_sessions.c.created_at.desc(), paper_sessions.c.id)
                .limit(1)
            )
            return dict(result) if result is not None else None
