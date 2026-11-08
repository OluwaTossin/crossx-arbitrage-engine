"""Real PostgreSQL checks; clean up only observations created by this test."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text

from crossx.db import Database, books, opportunities
from crossx.schemas import Level, Opportunity, OrderBook


@pytest.mark.integration
async def test_postgres_migration_and_exact_append_only_round_trip() -> None:
    url = os.environ.get("CROSSX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set CROSSX_TEST_DATABASE_URL to an Alembic-migrated PostgreSQL test database")
    database = Database(url)
    # A unique historical timestamp keeps cleanup isolated from real current observations.
    stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=uuid4().int % (365 * 86400 * 1_000_000)
    )
    book_ids: list[int] = []
    opportunity_ids: list[int] = []
    try:
        await database.initialize()
        async with database.engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
            )
            rows = (
                await connection.execute(
                    text(
                        "SELECT table_name, column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = current_schema() AND table_name IN "
                        "('book_observations', 'opportunities')"
                    )
                )
            ).all()
            types = {(row.table_name, row.column_name): row.data_type for row in rows}
            assert types[("book_observations", "payload")] == "jsonb"
            assert types[("opportunities", "payload")] == "jsonb"
            assert types[("book_observations", "observed_at")] == "timestamp with time zone"
            assert types[("book_observations", "received_at")] == "timestamp with time zone"
            assert types[("opportunities", "observed_at")] == "timestamp with time zone"
            assert not (
                await connection.execute(select(books.c.id).where(books.c.observed_at == stamp))
            ).all()
            assert not (
                await connection.execute(
                    select(opportunities.c.id).where(opportunities.c.observed_at == stamp)
                )
            ).all()
        book = OrderBook(
            exchange="kraken",
            symbol="BTC/USD",
            bids=(Level(price="100.123456789123456789", quantity="0.123456789123456789"),),
            asks=(Level(price="101.123456789123456789", quantity="1.123456789123456789"),),
            observed_at=stamp,
            received_at=stamp + timedelta(microseconds=1),
        )
        opportunity = Opportunity(
            symbol="BTC/USD",
            buy_exchange="kraken",
            sell_exchange="coinbase",
            quantity=Decimal("0.123456789123456789"),
            buy_vwap=Decimal("100.1"),
            sell_vwap=Decimal("101.2"),
            gross_profit=Decimal("0.1358024680358024679"),
            buy_fee=Decimal("0.01"),
            sell_fee=Decimal("0.02"),
            net_profit=Decimal("0.1058024680358024679"),
            observed_at=stamp,
        )
        await database.save_book(book)
        await database.save_book(book)
        await database.save_opportunity(opportunity)
        await database.save_opportunity(opportunity)
        async with database.engine.connect() as connection:
            saved_books = (
                (
                    await connection.execute(
                        select(books).where(books.c.observed_at == stamp).order_by(books.c.id)
                    )
                )
                .mappings()
                .all()
            )
            saved_opportunities = (
                (
                    await connection.execute(
                        select(opportunities)
                        .where(opportunities.c.observed_at == stamp)
                        .order_by(opportunities.c.id)
                    )
                )
                .mappings()
                .all()
            )
        book_ids = [row["id"] for row in saved_books]
        opportunity_ids = [row["id"] for row in saved_opportunities]
        assert len(set(book_ids)) == 2
        assert len(set(opportunity_ids)) == 2
        for row in saved_books:
            assert row["observed_at"] == stamp
            assert row["received_at"] == stamp + timedelta(microseconds=1)
            assert row["observed_at"].utcoffset() == timedelta(0)
            assert row["payload"]["bids"][0]["price"] == "100.123456789123456789"
            assert OrderBook.model_validate(row["payload"]) == book
        for row in saved_opportunities:
            assert row["payload"]["net_profit"] == "0.1058024680358024679"
            assert row["observed_at"] == stamp
            assert Opportunity.model_validate(row["payload"]) == opportunity
    finally:
        # ID predicates cannot touch other test or research observations.
        try:
            if book_ids or opportunity_ids:
                async with database.engine.begin() as connection:
                    if book_ids:
                        await connection.execute(delete(books).where(books.c.id.in_(book_ids)))
                    if opportunity_ids:
                        await connection.execute(
                            delete(opportunities).where(opportunities.c.id.in_(opportunity_ids))
                        )
        finally:
            await database.close()


@pytest.mark.integration
async def test_valid_exchange_replay_through_engine_persists_actual_signal() -> None:
    """Actual parsers, CRC, detector and database form one reproducible paper pipeline."""
    import json
    from pathlib import Path

    from crossx.adapters import CoinbaseAdapter, KrakenAdapter
    from crossx.app import ResearchEngine
    from crossx.config import Settings
    from crossx.schemas import FeeSchedule

    url = os.environ.get("CROSSX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set CROSSX_TEST_DATABASE_URL to an Alembic-migrated PostgreSQL test database")
    database = Database(url)
    stamp = datetime.now(UTC)
    cleanup_allowed = False
    try:
        await database.initialize()
        async with database.engine.connect() as connection:
            for table in (books, opportunities):
                assert not (
                    await connection.execute(select(table.c.id).where(table.c.observed_at == stamp))
                ).all()
        cleanup_allowed = True
        coinbase = CoinbaseAdapter(("BTC/USD",))
        kraken = KrakenAdapter(("BTC/USD",))
        frame = json.loads((Path(__file__).parent / "fixtures/coinbase_replay.json").read_text())[0]
        frame["timestamp"] = stamp.isoformat()
        coinbase_book = coinbase.process(frame, stamp)[0]
        kraken.books["BTC/USD"] = {
            "bids": {Decimal("110"): Decimal("2")},
            "asks": {Decimal("111"): Decimal("2")},
        }
        checksum = kraken.checksum("BTC/USD")
        kraken.reset()
        kraken_book = kraken.process(
            {
                "channel": "book",
                "type": "snapshot",
                "data": [
                    {
                        "symbol": "BTC/USD",
                        "bids": [{"price": "110", "qty": "2"}],
                        "asks": [{"price": "111", "qty": "2"}],
                        "checksum": checksum,
                    }
                ],
            },
            stamp,
        )[0]
        settings = Settings(
            database_url=url,
            symbols=("BTC/USD",),
            trade_quantity_btc=Decimal("1"),
            fees={venue: FeeSchedule(taker=Decimal("0.001")) for venue in ("kraken", "coinbase")},
        )
        subject = ResearchEngine(settings, database, {"kraken": kraken, "coinbase": coinbase})
        assert await subject.observe(coinbase_book, stamp) == []
        (signal,) = await subject.observe(kraken_book, stamp)
        assert signal.buy_exchange == "coinbase"
        assert signal.sell_exchange == "kraken"
        assert signal.quantity == Decimal("1")
        assert signal.net_profit == Decimal("8.5888")
        async with database.engine.connect() as connection:
            saved_books = (
                (
                    await connection.execute(
                        select(books).where(books.c.observed_at == stamp).order_by(books.c.id)
                    )
                )
                .mappings()
                .all()
            )
            saved_signals = (
                (
                    await connection.execute(
                        select(opportunities).where(opportunities.c.observed_at == stamp)
                    )
                )
                .mappings()
                .all()
            )
        assert [OrderBook.model_validate(row["payload"]) for row in saved_books] == [
            coinbase_book,
            kraken_book,
        ]
        assert len(saved_signals) == 1
        assert Opportunity.model_validate(saved_signals[0]["payload"]) == signal
        assert saved_signals[0]["payload"]["net_profit"] == "8.58880"
    finally:
        try:
            if cleanup_allowed:
                async with database.engine.begin() as connection:
                    for table in (opportunities, books):
                        inserted_ids = (
                            (
                                await connection.execute(
                                    select(table.c.id).where(table.c.observed_at == stamp)
                                )
                            )
                            .scalars()
                            .all()
                        )
                        if inserted_ids:
                            await connection.execute(
                                delete(table).where(table.c.id.in_(inserted_ids))
                            )
        finally:
            await database.close()


@pytest.mark.integration
async def test_batch_commit_is_exact_and_retention_plan_never_deletes():
    url = os.environ.get("CROSSX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated migrated PostgreSQL")
    database = Database(url)
    stamp = datetime(1960, 1, 1, tzinfo=UTC) + timedelta(microseconds=uuid4().int % 1000000000000)
    old = OrderBook(
        exchange="kraken",
        symbol="BTC/USD",
        observed_at=stamp,
        received_at=stamp,
        bids=(Level(price="100.123456789123456789", quantity="1"),),
        asks=(Level(price="101", quantity="2"),),
    )
    ids = []
    try:
        await database.save_books([old, old])
        async with database.engine.connect() as connection:
            saved = (
                (await connection.execute(select(books).where(books.c.received_at == stamp)))
                .mappings()
                .all()
            )
        ids = [row["id"] for row in saved]
        assert len(ids) == 2
        assert all(OrderBook.model_validate(row["payload"]) == old for row in saved)
        from sqlalchemy.exc import DBAPIError

        failed_stamp = stamp + timedelta(microseconds=1)
        valid = old.model_copy(update={"observed_at": failed_stamp, "received_at": failed_stamp})
        invalid = valid.model_copy(update={"exchange": "x" * 50})
        with pytest.raises(DBAPIError):
            await database.save_books([valid, invalid])
        async with database.engine.connect() as connection:
            assert not (
                await connection.execute(
                    select(books.c.id).where(books.c.received_at == failed_stamp)
                )
            ).all()
        assert await database.retention_plan(1) >= 2
        db_bytes, book_bytes = await database.storage_usage()
        assert db_bytes > 0 and book_bytes > 0
        async with database.engine.connect() as connection:
            assert (
                len((await connection.execute(select(books.c.id).where(books.c.id.in_(ids)))).all())
                == 2
            )
    finally:
        try:
            if ids:
                async with database.engine.begin() as connection:
                    await connection.execute(delete(books).where(books.c.id.in_(ids)))
        finally:
            await database.close()
