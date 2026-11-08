"""Deterministic ingestion-to-research checks without a database or live feed."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from crossx.adapters import CoinbaseAdapter, KrakenAdapter
from crossx.app import ResearchEngine
from crossx.config import Settings
from crossx.schemas import FeeSchedule, Level, Opportunity, OrderBook

NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


@dataclass
class FakeAdapter:
    synchronized_symbols: set = field(default_factory=lambda: {"BTC/USD", "ETH/USD"})
    generation: int = 0
    reconnect_count: int = 0
    integrity_error_count: int = 0


class FakeStore:
    def __init__(self, fail=None):
        self.events = []
        self.fail = fail

    async def save_book(self, book):
        if self.fail == "book":
            raise RuntimeError("book storage failed")
        self.events.append(book)

    async def save_opportunity(self, opportunity):
        if self.fail == "opportunity":
            raise RuntimeError("opportunity storage failed")
        self.events.append(opportunity)


def make_book(exchange="kraken", symbol="BTC/USD"):
    bid, ask = ("99", "100") if exchange == "kraken" else ("110", "111")
    return OrderBook(
        exchange=exchange,
        symbol=symbol,
        bids=(Level(price=Decimal(bid), quantity=Decimal("2")),),
        asks=(Level(price=Decimal(ask), quantity=Decimal("2")),),
        observed_at=NOW,
        received_at=NOW,
    )


def engine(*, store=None, known_fees=True):
    config = {"trade_quantity_btc": Decimal("1"), "trade_quantity_eth": Decimal(".5")}
    if known_fees:
        config["fees"] = {
            venue: FeeSchedule(taker=Decimal(".01")) for venue in ("kraken", "coinbase")
        }
    return ResearchEngine(
        Settings(**config),
        store or FakeStore(),
        {venue: FakeAdapter() for venue in ("kraken", "coinbase")},
    )


@pytest.mark.asyncio
async def test_engine_persists_before_recording_fee_aware_opportunity():
    subject = engine()
    first, second = make_book(), make_book("coinbase")
    assert await subject.observe(first, NOW) == []
    (opportunity,) = await subject.observe(second, NOW)
    assert subject.store.events == [first, second, opportunity]
    assert opportunity.net_profit == Decimal("7.9")
    assert opportunity.quantity == Decimal("1")


@pytest.mark.asyncio
async def test_eth_uses_eth_quantity():
    subject = engine()
    await subject.observe(make_book(symbol="ETH/USD"), NOW)
    (result,) = await subject.observe(make_book("coinbase", "ETH/USD"), NOW)
    assert result.quantity == Decimal(".5")
    assert result.net_profit == Decimal("3.95")


@pytest.mark.asyncio
async def test_unknown_fees_preserve_books_without_opportunities():
    subject = engine(known_fees=False)
    await subject.observe(make_book(), NOW)
    assert await subject.observe(make_book("coinbase"), NOW) == []
    assert len(subject.store.events) == 2
    assert all(isinstance(row, OrderBook) for row in subject.store.events)


@pytest.mark.asyncio
async def test_counterpart_loss_of_sync_immediately_disables_cached_book():
    subject = engine()
    await subject.observe(make_book(), NOW)
    subject.adapters["kraken"].synchronized_symbols.clear()
    assert await subject.observe(make_book("coinbase"), NOW) == []
    assert len(subject.store.events) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", ["book", "opportunity"])
async def test_storage_failures_propagate(fail):
    store = FakeStore()
    subject = engine(store=store)
    await subject.observe(make_book(), NOW)
    store.fail = fail
    with pytest.raises(RuntimeError, match=f"{fail} storage failed"):
        await subject.observe(make_book("coinbase"), NOW)
    assert not any(isinstance(row, Opportunity) for row in store.events)
    if fail == "book":
        assert ("coinbase", "BTC/USD") not in subject.latest


@pytest.mark.asyncio
async def test_old_queued_generation_is_persisted_but_excluded():
    subject = engine()
    subject.adapters["kraken"].generation = 1
    queued = make_book()
    assert await subject.observe(queued, NOW, generation=0) == []
    assert queued in subject.store.events
    assert await subject.observe(make_book("coinbase"), NOW, generation=0) == []
    assert ("kraken", "BTC/USD") not in subject.latest


@pytest.mark.asyncio
async def test_reset_and_resnapshot_cannot_reactivate_cached_generation():
    subject = engine()
    await subject.observe(make_book(), NOW, generation=0)
    subject.adapters["kraken"].generation = 1
    # The adapter is synchronized after a new snapshot, but its old cached book
    # must stay excluded until the new generation's snapshot reaches the engine.
    assert await subject.observe(make_book("coinbase"), NOW, generation=0) == []
    found = await subject.observe(make_book(), NOW, generation=1)
    assert len(found) == 1


@pytest.mark.asyncio
async def test_adapter_replay_through_engine_persists_validated_observations():
    coinbase = CoinbaseAdapter(("BTC/USD",))
    kraken = KrakenAdapter(("BTC/USD",))
    fixture = Path(__file__).parent / "fixtures/coinbase_replay.json"
    coinbase_book = coinbase.process(json.loads(fixture.read_text())[0], NOW)[0]
    # Build a deterministic valid CRC frame whose bid exceeds Coinbase's ask.
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
        NOW,
    )[0]
    subject = engine()
    subject.adapters = {"kraken": kraken, "coinbase": coinbase}
    await subject.observe(coinbase_book, NOW)
    (found,) = await subject.observe(kraken_book, NOW)
    assert found.buy_exchange == "coinbase"
    assert found.sell_exchange == "kraken"
    assert found.net_profit == Decimal("6.688")
    assert subject.store.events == [coinbase_book, kraken_book, found]
