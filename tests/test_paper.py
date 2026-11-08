from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from crossx.paper import VENUES, MarketRules, PaperConfig, Simulator
from crossx.schemas import FeeSchedule, Level, OrderBook

T = datetime(2026, 1, 1, tzinfo=UTC)


def config(**changes):
    values = dict(
        gbp_usd="1.25",
        assumption_at=T,
        capital_gbp="1000",
        fx_cost_rate=0,
        funding_fee_rate=0,
        rebalancing_cost_usd=0,
        slippage_rate=0,
        min_profit_usd=0,
        min_return=0,
        max_exchange_fraction="0.9",
        max_inventory_fraction="0.99",
        max_trade_usd=1000,
        latency_ms=10,
        second_leg_latency_ms=10,
        trade_quantity_btc="0.1",
        trade_quantity_eth="0.1",
        fees={v: FeeSchedule(taker="0.001") for v in VENUES},
        rules={
            f"{v}:{s}": MarketRules(
                quantity_step="0.001", min_quantity="0.001", min_notional="1", max_quantity="10"
            )
            for v in VENUES
            for s in ("BTC/USD", "ETH/USD")
        },
    )
    values.update(changes)
    return PaperConfig(**values)


def book(venue="kraken", symbol="BTC/USD", bid="99", ask="100", quantity="10", ms=0):
    t = T + timedelta(milliseconds=ms)
    return OrderBook(
        exchange=venue,
        symbol=symbol,
        bids=(Level(price=bid, quantity=quantity),),
        asks=(Level(price=ask, quantity=quantity),),
        observed_at=t,
        received_at=t,
    )


def seeded(**changes):
    events = []
    sim = Simulator(config(**changes), events.append)
    for v in VENUES:
        for s in ("BTC/USD", "ETH/USD"):
            sim.ingest(book(v, s))
    return sim, events


def trigger(sim):
    sim.ingest(book("coinbase", bid="110", ask="111", ms=1))


def test_capital_not_duplicated_and_small_capital_fragmentation():
    for capital in (500, 1000, 1500):
        sim, _ = seeded(capital_gbp=capital)
        basis = sum(
            (
                w.cash + sum((p.cost for p in w.positions.values()), D(0))
                for w in sim.wallets.values()
            ),
            D(0),
        )
        assert basis == D(capital) * D("1.25")
        assert sim.initial_nav < basis  # Bid valuation includes initial spread.
    sim, events = seeded(capital_gbp=1)
    trigger(sim)
    assert any(e.get("reason") == "insufficient_capital" for e in events)


def test_reservation_settlement_and_balances():
    sim, events = seeded()
    trigger(sim)
    assert sim.pending
    assert sim.wallets["kraken"].reserved_cash > 0
    assert sim.wallets["coinbase"].reserved["BTC"] == D(".1")
    before = sum((w.positions["BTC"].quantity for w in sim.wallets.values()), D(0))
    result = sim.finish()
    after = sum((w.positions["BTC"].quantity for w in sim.wallets.values()), D(0))
    assert before == after
    assert result["counts"]["trade_completed"] == 1
    assert D(result["paired_cashflow_pnl_usd"]) == D(".979")
    assert all(w.reserved_cash == 0 for w in sim.wallets.values())
    assert sim.wallets["coinbase"].reserved["BTC"] == 0
    assert sim.fees_paid == D(".021")
    assert [e["side"] for e in events if e["kind"] == "leg"] == ["buy", "sell"]


def test_second_leg_failure_retains_unhedged_exposure():
    sim, events = seeded(fail_second_leg=True)
    trigger(sim)
    result = sim.finish()
    assert result["unhedged"]["BTC"] == "0.1"
    assert result["counts"]["trade_unhedged"] == 1
    assert D(result["paired_cashflow_pnl_usd"]) == 0
    assert [e for e in events if e["kind"] == "leg"][-1]["status"] == "failed"


def test_partial_second_leg_and_first_leg_depth():
    sim, events = seeded(second_leg_fill_fraction="0.5")
    trigger(sim)
    result = sim.finish()
    assert D(result["unhedged"]["BTC"]) == D(".05")
    assert [e for e in events if e["kind"] == "leg"][-1]["status"] == "partial"
    sim, events = seeded()
    trigger(sim)
    sim.ingest(book("kraken", quantity=".03", ms=5))
    result = sim.finish()
    assert [e for e in events if e["kind"] == "leg"][0]["quantity"] == "0.03"
    assert D(result["unhedged"]["BTC"]) == 0


def test_no_lookahead_due_leg_uses_previous_book():
    sim, events = seeded()
    trigger(sim)
    sim.ingest(book("coinbase", bid="500", ask="501", ms=30))
    legs = [e for e in events if e["kind"] == "leg"]
    assert legs[1]["notional"] == "11.0"  # 110 at due time; future 500 book unavailable then.


def test_repeated_unchanged_liquidity_not_reused():
    sim, _ = seeded()
    trigger(sim)
    sim.finish()
    b = sim.latest[("kraken", "BTC/USD")]
    fill = sim.fill(b, "buy", D("10"), D(".001"))
    assert fill.quantity == D("9.9")
    sim.ingest(book("kraken", ms=30))
    assert sim.fill(sim.latest[("kraken", "BTC/USD")], "buy", D("10"), D(".001")).quantity == D(
        "9.9"
    )


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"fees": {}}, "unknown_fee"),
        ({"rules": {}}, "unknown_market_rules"),
        ({"max_trade_usd": "1"}, "trade_limit"),
        ({"min_profit_usd": "100"}, "net_threshold"),
        ({"max_exchange_fraction": ".1"}, "exchange_exposure"),
        ({"max_inventory_fraction": ".1"}, "inventory_concentration"),
    ],
)
def test_fail_closed_rejections(changes, reason):
    sim, events = seeded(**changes)
    trigger(sim)
    assert any(e.get("reason") == reason for e in events)
    assert not sim.pending


def test_stale_gap_and_unsynchronized_data():
    sim, events = seeded()
    sim.ingest(book("coinbase", bid="110", ask="111", ms=6000))
    assert not sim.pending
    assert any(e.get("reason") == "stale_or_unsynchronized" for e in events)
    with pytest.raises(ValueError, match="receipt"):
        sim.ingest(book(ms=0))


def test_selective_allocation_and_empty_data():
    sim, _ = seeded(allocation="selective")
    assert sim.wallets["binance"].cash == 0
    assert sim.wallets["kraken"].cash > D(300)
    empty = Simulator(config(), lambda e: None).finish()
    assert empty["missing_data"] is True
    assert empty["total_pnl_usd"] is None
    assert empty["nav_usd"] is None


def test_drawdown_and_loss_limit():
    sim, events = seeded(max_loss_usd=".01")
    sim.ingest(book("kraken", bid="80", ask="81", ms=1))
    assert sim.drawdown > 0
    assert any(e.get("reason") == "loss_limit" for e in events)
    assert not sim.pending


def test_deterministic_replay():
    outputs = []
    for _ in range(2):
        sim, events = seeded()
        trigger(sim)
        outputs.append((sim.finish(), events))
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_invalid_capital(value):
    with pytest.raises(ValidationError):
        config(capital_gbp=value)


def test_initial_allocation_is_not_repeated_on_updates():
    sim, _ = seeded()
    initial = sim.initial_nav
    sim.wallets["kraken"].cash -= D(1)
    changed = sim.wallets["kraken"].cash
    sim.ingest(book("kraken", ms=1))
    assert sim.wallets["kraken"].cash == changed
    assert sim.initial_nav == initial


def test_market_minimum_and_available_depth():
    sim, events = seeded(trade_quantity_btc="0.0001")
    trigger(sim)
    assert any(e.get("reason") == "quantity_increment" for e in events)
    sim, events = seeded(trade_quantity_btc="11")
    trigger(sim)
    assert any(e.get("reason") == "insufficient_liquidity" for e in events)


def test_missing_or_late_second_leg_is_unavailable_not_guaranteed():
    sim, events = seeded(second_leg_latency_ms=6000)
    trigger(sim)
    result = sim.finish()
    assert D(result["unhedged"]["BTC"]) == D(".1")
    assert result["stale_valuation_marks"]
    assert any(e.get("status") == "failed" and e.get("side") == "sell" for e in events)


def test_inventory_depletion_requires_rebalancing():
    sim, events = seeded()
    sim.wallets["coinbase"].positions["BTC"].quantity = D(0)
    sim.wallets["coinbase"].positions["BTC"].cost = D(0)
    trigger(sim)
    assert any(e.get("reason") == "inventory_depleted" for e in events)


def test_four_venue_directions_and_unknown_fees():
    from itertools import permutations

    from crossx.intelligence import detect_opportunities

    all_books = [book(v, bid=str(99 + i * 3), ask=str(100 + i * 3)) for i, v in enumerate(VENUES)]
    assert len(list(permutations(all_books, 2))) == 12
    found = detect_opportunities(all_books, {v: FeeSchedule(taker=0) for v in VENUES}, D(".1"), T)
    assert len(found) == 6
    assert len({(x.buy_exchange, x.sell_exchange) for x in found}) == 6
    assert detect_opportunities(all_books, {}, D(".1"), T) == []


def test_evaluation_rejects_quote_mismatch_without_parity():
    sim, events = seeded()
    a = book().model_copy(update={"symbol": "BTC/USDT"})
    b = book("coinbase", bid="110", ask="111").model_copy(update={"symbol": "BTC/USDT"})
    sim.consider(a, b)
    assert any(e.get("reason") == "quote_conversion_unavailable" for e in events)


def test_slippage_and_fees_can_remove_positive_spread():
    sim, events = seeded(slippage_rate=".1")
    trigger(sim)
    assert any(e.get("reason") == "net_threshold" for e in events)
    assert not sim.pending


def test_total_pnl_includes_conversion_funding_and_rebalance_cost_label():
    sim, _ = seeded(fx_cost_rate=".01", funding_fee_rate=".01", rebalancing_cost_usd=".5")
    result = sim.finish()
    assert D(result["total_pnl_usd"]) < D(result["trading_pnl_usd"])
    trigger(sim)
    result = sim.finish()
    assert D(result["paired_pnl_after_assumed_rebalance_usd"]) < D(
        result["paired_cashflow_pnl_usd"]
    )


def test_shared_liquidity_survives_wire_precision_and_unrelated_update():
    sim, _ = seeded()
    trigger(sim)
    sim.finish()
    b = sim.latest[("kraken", "BTC/USD")]
    updated = b.model_copy(
        update={
            "asks": (Level(price="100.00", quantity="10.000"), Level(price="102", quantity="1"))
        }
    )
    f = sim.fill(updated, "buy", D(20), D(0))
    assert f.quantity == D("10.9")


def test_venue_results_reconcile_aggregate_and_rebalance_blocks_signal():
    sim, _ = seeded()
    trigger(sim)
    result = sim.finish()
    assert sum((D(w["nav_usd"]) for w in result["balances"].values()), D(0)) == D(result["nav_usd"])
    assert sum((D(w["realized_pnl_usd"]) for w in result["balances"].values()), D(0)) == D(
        result["realized_inventory_pnl_usd"]
    )
    sim, events = seeded(rebalancing_cost_usd=2)
    trigger(sim)
    assert not sim.pending
    assert any(e.get("reason") == "net_threshold" for e in events)
