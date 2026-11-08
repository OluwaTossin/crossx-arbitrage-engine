"""Deterministic spot-only paper ledger. No transport, credentials or exchange orders.

USD markets only; stablecoin conversion fails closed. Events use local receipt
ordering. Pending simulated legs see only books available at their due time.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from itertools import permutations
from typing import Literal

from pydantic import Field, model_validator

from crossx.schemas import Exchange, FeeSchedule, FrozenModel, OrderBook, Symbol, utc_timestamp

D = Decimal
VENUES: tuple[Exchange, ...] = ("kraken", "coinbase", "binance", "cryptocom")


class MarketRules(FrozenModel):
    quantity_step: Decimal = Field(gt=0)
    min_quantity: Decimal = Field(gt=0)
    min_notional: Decimal = Field(gt=0)
    max_quantity: Decimal = Field(gt=0)

    def accepts(self, quantity: Decimal, notional: Decimal) -> bool:
        return (
            self.min_quantity <= quantity <= self.max_quantity
            and quantity % self.quantity_step == 0
            and notional >= self.min_notional
        )


class PaperConfig(FrozenModel):
    capital_gbp: Decimal = Field(default=D(1000), gt=0)
    gbp_usd: Decimal = Field(gt=0)  # Explicit valuation assumption, never fetched or defaulted.
    assumption_at: datetime
    fx_cost_rate: Decimal = Field(default=D("0.005"), ge=0, lt=1)
    funding_fee_rate: Decimal = Field(default=D("0.01"), ge=0, lt=1)
    venues: tuple[Exchange, ...] = VENUES
    allocation: Literal["equal", "selective"] = "equal"
    selected_venues: tuple[Exchange, ...] = ("kraken", "coinbase")
    inventory_fraction: Decimal = Field(default=D("0.5"), ge=0, le=1)
    fees: dict[Exchange, FeeSchedule] = Field(default_factory=dict)
    # Keys are venue + ':' + actual symbol; missing rules fail closed.
    rules: dict[str, MarketRules] = Field(default_factory=dict)
    trade_quantity_btc: Decimal = Field(default=D("0.001"), gt=0)
    trade_quantity_eth: Decimal = Field(default=D("0.01"), gt=0)
    max_trade_usd: Decimal = Field(default=D(50), gt=0)
    max_exchange_fraction: Decimal = Field(default=D("0.6"), gt=0, le=1)
    max_inventory_fraction: Decimal = Field(default=D("0.75"), gt=0, le=1)
    min_profit_usd: Decimal = Field(default=D("0.1"), ge=0)
    min_return: Decimal = Field(default=D("0.002"), ge=0)
    max_loss_usd: Decimal = Field(default=D(25), gt=0)
    max_drawdown_fraction: Decimal = Field(default=D("0.1"), gt=0, le=1)
    slippage_rate: Decimal = Field(default=D("0.002"), ge=0, lt=1)
    latency_ms: int = Field(default=250, ge=0, le=60000)
    second_leg_latency_ms: int = Field(default=250, ge=0, le=60000)
    max_age_seconds: int = Field(default=5, gt=0)
    max_skew_seconds: int = Field(default=2, ge=0)
    # Deterministic fault assumption, explicitly reported; default no forced failure.
    fail_second_leg: bool = False
    second_leg_fill_fraction: Decimal = Field(default=D(1), ge=0, le=1)
    rebalancing_cost_usd: Decimal = Field(default=D(5), ge=0)

    @model_validator(mode="after")
    def valid(self) -> PaperConfig:
        utc_timestamp(self.assumption_at)
        if not self.venues or len(set(self.venues)) != len(self.venues):
            raise ValueError("unique nonempty venues required")
        if (
            not self.selected_venues
            or len(set(self.selected_venues)) != len(self.selected_venues)
            or not set(self.selected_venues) <= set(self.venues)
        ):
            raise ValueError("selected venues must be unique and in venues")
        return self


@dataclass
class Position:
    quantity: Decimal = D(0)
    cost: Decimal = D(0)


@dataclass
class Wallet:
    initial_nav: Decimal = D(0)
    realized_pnl: Decimal = D(0)
    fees_paid: Decimal = D(0)
    cash: Decimal = D(0)
    reserved_cash: Decimal = D(0)
    reserved: dict[str, Decimal] = field(default_factory=dict)
    positions: dict[str, Position] = field(
        default_factory=lambda: {"BTC": Position(), "ETH": Position()}
    )


@dataclass
class Trade:
    number: int
    buy: Exchange
    sell: Exchange
    symbol: Symbol
    requested: Decimal
    cash_reserved: Decimal
    created: datetime
    buy_quantity: Decimal = D(0)
    buy_cost: Decimal = D(0)
    sell_quantity: Decimal = D(0)
    sell_proceeds: Decimal = D(0)


@dataclass(frozen=True)
class Fill:
    quantity: Decimal
    notional: Decimal
    fee: Decimal


class Simulator:
    """One bounded state machine per scenario; caller streams evidence to a sink."""

    def __init__(self, config: PaperConfig, emit: Callable[[dict[str, object]], None]) -> None:
        self.config = config
        self.emit = emit
        self.latest: dict[tuple[Exchange, Symbol], OrderBook] = {}
        self.wallets = {v: Wallet() for v in config.venues}
        self.pending: list[tuple[datetime, int, str, Trade]] = []
        self.consumed: dict[tuple[Exchange, Symbol, str], dict[Decimal, Decimal]] = {}
        self.now: datetime | None = None
        self.initialized = False
        self.sequence = 0
        self.event_sequence = 0
        self.initial_nav = D(0)
        self.peak_nav = D(0)
        self.drawdown = D(0)
        self.realized_pnl = D(0)
        self.paired_pnl = D(0)
        self.fees_paid = D(0)
        self.unhedged: dict[str, Decimal] = {"BTC": D(0), "ETH": D(0)}
        self.counts: dict[str, int] = {}
        self.rejections: dict[str, int] = {}

    def event(self, kind: str, **details: object) -> None:
        self.event_sequence += 1
        self.counts[kind] = self.counts.get(kind, 0) + 1
        self.emit(
            {
                "sequence": self.event_sequence,
                "at": self.now.isoformat() if self.now else None,
                "kind": kind,
                **details,
            }
        )

    def fresh(self, book: OrderBook, now: datetime) -> bool:
        return book.synchronized and all(
            0 <= (now - t).total_seconds() <= self.config.max_age_seconds
            for t in (book.observed_at, book.received_at)
        )

    def book(self, venue: Exchange, symbol: Symbol) -> OrderBook | None:
        b = self.latest.get((venue, symbol))
        return b if b and self.now and self.fresh(b, self.now) else None

    def active_venues(self) -> tuple[Exchange, ...]:
        return (
            self.config.venues if self.config.allocation == "equal" else self.config.selected_venues
        )

    def bootstrap(self) -> None:
        if self.initialized:
            return
        active = self.active_venues()
        symbols: tuple[Symbol, ...] = ("BTC/USD", "ETH/USD")
        if not all(self.book(v, s) for v in active for s in symbols):
            return
        total = self.config.capital_gbp * self.config.gbp_usd * (1 - self.config.fx_cost_rate)
        usable = total * (1 - self.config.funding_fee_rate)
        allocation = usable / len(active)
        for v in active:
            wallet = self.wallets[v]
            wallet.cash = allocation * (1 - self.config.inventory_fraction)
            for symbol in ("BTC/USD", "ETH/USD"):
                book = self.book(v, symbol)  # Already verified above.
                assert book is not None
                price = book.asks[0].price
                amount = allocation * self.config.inventory_fraction / 2
                # Initial inventory is an explicit mark-based endowment, not a fabricated fill.
                wallet.positions[book.base_asset] = Position(amount / price, amount)
        self.initialized = True
        self.initial_nav = self.nav()
        for venue, wallet in self.wallets.items():
            wallet.initial_nav = self.nav(venue)
        self.peak_nav = self.initial_nav
        self.event(
            "allocation",
            starting_gbp=str(self.config.capital_gbp),
            gross_usd=str(self.config.capital_gbp * self.config.gbp_usd),
            conversion_and_funding_cost=str(self.config.capital_gbp * self.config.gbp_usd - usable),
            allocation=self.config.allocation,
            nav=str(self.initial_nav),
        )

    def nav(self, venue: Exchange | None = None) -> Decimal:
        result = D(0)
        for v, w in self.wallets.items():
            if venue is not None and venue != v:
                continue
            result += w.cash
            for asset, pos in w.positions.items():
                symbol: Symbol = "BTC/USD" if asset == "BTC" else "ETH/USD"
                b = self.latest.get((v, symbol))
                result += pos.quantity * b.bids[0].price if b else pos.cost
        return result

    def liquidity(self, book: OrderBook, side: str) -> dict[Decimal, Decimal]:
        levels = book.asks if side == "buy" else book.bids
        key = book.exchange, book.symbol, side
        used = self.consumed.setdefault(key, {})
        active = {level.price for level in levels}
        # Preserve simulated consumption across wire precision changes and unrelated
        # level updates. No assumed replenishment while a price remains visible.
        for price in list(used):
            if price not in active:
                del used[price]
        return used

    def fill(
        self, book: OrderBook, side: str, quantity: Decimal, rate: Decimal, consume: bool = False
    ) -> Fill:
        levels = book.asks if side == "buy" else book.bids
        used = self.liquidity(book, side)
        remaining, notional = quantity, D(0)
        taken: dict[Decimal, Decimal] = {}
        adjustment = (
            1 + self.config.slippage_rate if side == "buy" else 1 - self.config.slippage_rate
        )
        for level in levels:
            amount = min(remaining, max(D(0), level.quantity - used.get(level.price, D(0))))
            taken[level.price] = amount
            notional += amount * level.price * adjustment
            remaining -= amount
            if not remaining:
                break
        if consume:
            for price, amount in taken.items():
                used[price] = used.get(price, D(0)) + amount
        return Fill(quantity - remaining, notional, notional * rate)

    def reject(self, reason: str, buy: OrderBook, sell: OrderBook) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1
        self.event(
            "rejected", reason=reason, buy=buy.exchange, sell=sell.exchange, symbol=buy.symbol
        )

    def consider(self, buy: OrderBook, sell: OrderBook) -> None:
        c = self.config
        if buy.symbol != sell.symbol or buy.exchange == sell.exchange:
            return
        if buy.quote_asset != "USD" or sell.quote_asset != "USD":
            self.reject("quote_conversion_unavailable", buy, sell)
            return
        if self.now is None or not self.fresh(buy, self.now) or not self.fresh(sell, self.now):
            self.reject("stale_or_unsynchronized", buy, sell)
            return
        if abs((buy.observed_at - sell.observed_at).total_seconds()) > c.max_skew_seconds:
            self.reject("clock_skew", buy, sell)
            return
        q = c.trade_quantity_btc if buy.base_asset == "BTC" else c.trade_quantity_eth
        raw = sell.bids[0].price - buy.asks[0].price
        if raw <= 0:
            return
        self.event(
            "raw_spread", symbol=buy.symbol, buy=buy.exchange, sell=sell.exchange, spread=str(raw)
        )
        schedules = c.fees.get(buy.exchange), c.fees.get(sell.exchange)
        rates = [f.taker if f else None for f in schedules]
        if rates[0] is None or rates[1] is None:
            self.reject("unknown_fee", buy, sell)
            return
        br, sr = rates[0], rates[1]
        assert br is not None and sr is not None
        rules = [c.rules.get(v + ":" + buy.symbol) for v in (buy.exchange, sell.exchange)]
        if any(r is None for r in rules):
            self.reject("unknown_market_rules", buy, sell)
            return
        for r in rules:
            assert r is not None
            q = (q / r.quantity_step).to_integral_value(rounding=ROUND_DOWN) * r.quantity_step
        # Two-step rounding can violate the other venue's increment; both are checked below.
        if q <= 0:
            self.reject("quantity_increment", buy, sell)
            return
        bf, sf = self.fill(buy, "buy", q, br), self.fill(sell, "sell", q, sr)
        if bf.quantity != q or sf.quantity != q:
            self.reject("insufficient_liquidity", buy, sell)
            return
        for r, f in zip(rules, (bf, sf), strict=True):
            assert r is not None
            if not r.accepts(q, f.notional):
                self.reject("order_constraints", buy, sell)
                return
        cost, proceeds = bf.notional + bf.fee, sf.notional - sf.fee
        trading_net = proceeds - cost
        net = trading_net - c.rebalancing_cost_usd
        self.event(
            "theoretical", net_usd=str(net), trading_net_usd=str(trading_net), symbol=buy.symbol
        )
        if net <= c.min_profit_usd or net / cost < c.min_return:
            self.reject("net_threshold", buy, sell)
            return
        bw, sw = self.wallets[buy.exchange], self.wallets[sell.exchange]
        if cost > c.max_trade_usd:
            self.reject("trade_limit", buy, sell)
            return
        if bw.cash - bw.reserved_cash < cost:
            self.reject("insufficient_capital", buy, sell)
            return
        if sw.positions[buy.base_asset].quantity - sw.reserved.get(buy.base_asset, D(0)) < q:
            self.reject("inventory_depleted", buy, sell)
            return
        nav = self.nav()
        if self.initial_nav - nav >= c.max_loss_usd or self.drawdown >= c.max_drawdown_fraction:
            self.reject("loss_limit", buy, sell)
            return
        if any(self.nav(v) > nav * c.max_exchange_fraction for v in (buy.exchange, sell.exchange)):
            self.reject("exchange_exposure", buy, sell)
            return
        inventory = sum(
            (
                p.quantity * self.latest[(v, "BTC/USD" if a == "BTC" else "ETH/USD")].bids[0].price
                for v, w in self.wallets.items()
                for a, p in w.positions.items()
                if (v, "BTC/USD" if a == "BTC" else "ETH/USD") in self.latest
            ),
            D(0),
        )
        # Include pending buys conservatively; no assumed offset before second-leg settlement.
        if (
            inventory + sum((w.reserved_cash for w in self.wallets.values()), D(0)) + cost
            > nav * c.max_inventory_fraction
        ):
            self.reject("inventory_concentration", buy, sell)
            return
        self.sequence += 1
        trade = Trade(self.sequence, buy.exchange, sell.exchange, buy.symbol, q, cost, self.now)
        bw.reserved_cash += cost
        sw.reserved[buy.base_asset] = sw.reserved.get(buy.base_asset, D(0)) + q
        self.event("capital_feasible", trade=trade.number, net_usd=str(net))
        heapq.heappush(
            self.pending,
            (self.now + timedelta(milliseconds=c.latency_ms), trade.number, "buy", trade),
        )

    def execute(self, side: str, trade: Trade) -> None:
        c = self.config
        asset = trade.symbol.split("/")[0]
        venue = trade.buy if side == "buy" else trade.sell
        book = self.book(venue, trade.symbol)
        wallet = self.wallets[venue]
        rate = c.fees[venue].taker
        assert rate is not None
        quantity = (
            trade.requested if side == "buy" else trade.buy_quantity * c.second_leg_fill_fraction
        )
        if side == "sell" and c.fail_second_leg:
            book = None
        fill = self.fill(book, side, quantity, rate) if book else Fill(D(0), D(0), D(0))
        rules = c.rules[venue + ":" + trade.symbol]
        q = (fill.quantity / rules.quantity_step).to_integral_value(
            rounding=ROUND_DOWN
        ) * rules.quantity_step
        fill = self.fill(book, side, q, rate) if book else fill
        if (
            not q
            or not rules.accepts(q, fill.notional)
            or (side == "buy" and fill.notional + fill.fee > trade.cash_reserved)
        ):
            fill = Fill(D(0), D(0), D(0))
        if fill.quantity and book:
            fill = self.fill(book, side, fill.quantity, rate, consume=True)
            self.fees_paid += fill.fee
            wallet.fees_paid += fill.fee
            pos = wallet.positions[asset]
            if side == "buy":
                cost = fill.notional + fill.fee
                wallet.cash -= cost
                pos.quantity += fill.quantity
                pos.cost += cost
                trade.buy_quantity, trade.buy_cost = fill.quantity, cost
            else:
                proceeds = fill.notional - fill.fee
                basis = pos.cost * fill.quantity / pos.quantity
                pos.quantity -= fill.quantity
                pos.cost -= basis
                wallet.cash += proceeds
                self.realized_pnl += proceeds - basis
                wallet.realized_pnl += proceeds - basis
                trade.sell_quantity, trade.sell_proceeds = fill.quantity, proceeds
        self.event(
            "leg",
            trade=trade.number,
            side=side,
            venue=venue,
            symbol=trade.symbol,
            quantity=str(fill.quantity),
            notional=str(fill.notional),
            fee=str(fill.fee),
            status="filled"
            if fill.quantity == trade.requested
            else "partial"
            if fill.quantity
            else "failed",
        )
        if fill.quantity < trade.requested:
            self.event(
                "partial_fill" if fill.quantity else "failed_leg", trade=trade.number, side=side
            )
        if side == "buy":
            wallet.reserved_cash -= trade.cash_reserved
            if fill.quantity:
                assert self.now is not None
                heapq.heappush(
                    self.pending,
                    (
                        self.now + timedelta(milliseconds=c.second_leg_latency_ms),
                        trade.number,
                        "sell",
                        trade,
                    ),
                )
            else:
                self.wallets[trade.sell].reserved[asset] -= trade.requested
                self.event("trade_failed", trade=trade.number, reason="first_leg_unavailable")
        else:
            wallet.reserved[asset] -= trade.requested
            unhedged = trade.buy_quantity - trade.sell_quantity
            self.unhedged[asset] += unhedged
            paired = trade.sell_proceeds - (
                trade.buy_cost * trade.sell_quantity / trade.buy_quantity
            )
            self.paired_pnl += paired
            self.event(
                "trade_completed" if not unhedged else "trade_unhedged",
                trade=trade.number,
                matched_quantity=str(trade.sell_quantity),
                unhedged_quantity=str(unhedged),
                paired_pnl_usd=str(paired),
                rebalancing_required=True,
                assumed_rebalancing_cost_usd=str(c.rebalancing_cost_usd),
            )
        self.mark()

    def mark(self) -> None:
        nav = self.nav()
        self.peak_nav = max(self.peak_nav, nav)
        if self.peak_nav:
            self.drawdown = max(self.drawdown, (self.peak_nav - nav) / self.peak_nav)

    def advance(self, now: datetime) -> None:
        while self.pending and self.pending[0][0] <= now:
            due, _, side, trade = heapq.heappop(self.pending)
            self.now = due
            self.execute(side, trade)
        self.now = now

    def ingest(self, book: OrderBook) -> None:
        utc_timestamp(book.received_at)
        if self.now is not None and book.received_at < self.now:
            raise ValueError("Replay must be ordered by local receipt time")
        if book.exchange not in self.wallets:
            raise ValueError("Observation outside scenario venues")
        with localcontext() as ctx:
            ctx.prec = 50
            self.advance(
                book.received_at
            )  # Due legs execute before this new observation: no look-ahead.
            old = self.latest.get((book.exchange, book.symbol))
            if old and book.observed_at < old.observed_at:
                self.event("data_gap", reason="event_time_regression", venue=book.exchange)
                self.latest.pop((book.exchange, book.symbol), None)
                return
            self.latest[(book.exchange, book.symbol)] = book
            self.bootstrap()
            if not self.initialized:
                return
            self.mark()
            current = [
                b
                for b in self.latest.values()
                if b.symbol == book.symbol and b.exchange in self.active_venues()
            ]
            # One pending trade per symbol avoids overlapping shared-leg reservations.
            if not any(t.symbol == book.symbol for _, _, _, t in self.pending):
                for buy, sell in permutations(current, 2):
                    self.consider(buy, sell)
                    if any(t.symbol == book.symbol for _, _, _, t in self.pending):
                        break

    def finish(self) -> dict[str, object]:
        with localcontext() as ctx:
            ctx.prec = 50
            while self.pending:
                self.advance(self.pending[0][0])
            stale = [
                f"{v}:{s}"
                for (v, s), b in self.latest.items()
                if self.now and not self.fresh(b, self.now)
            ]
            nav = self.nav()
            balances = {
                v: {
                    "cash_usd": str(w.cash),
                    "nav_usd": str(self.nav(v)),
                    "trading_pnl_usd": str(self.nav(v) - w.initial_nav),
                    "realized_pnl_usd": str(w.realized_pnl),
                    "fees_usd": str(w.fees_paid),
                    "reserved_inventory": {a: str(q) for a, q in w.reserved.items()},
                    "reserved_cash": str(w.reserved_cash),
                    "inventory": {
                        a: {"quantity": str(p.quantity), "cost_usd": str(p.cost)}
                        for a, p in w.positions.items()
                    },
                }
                for v, w in self.wallets.items()
            }
            result: dict[str, object] = {
                "initialized": self.initialized,
                "missing_data": not self.initialized,
                "capital_gbp": str(self.config.capital_gbp),
                "nav_usd": str(nav),
                "initial_nav_usd": str(self.initial_nav),
                "trading_pnl_usd": str(nav - self.initial_nav),
                "total_pnl_usd": str(nav - self.config.capital_gbp * self.config.gbp_usd),
                "realized_inventory_pnl_usd": str(self.realized_pnl),
                "unrealized_inventory_pnl_usd": str(nav - self.initial_nav - self.realized_pnl),
                "paired_cashflow_pnl_usd": str(self.paired_pnl),
                "paired_pnl_after_assumed_rebalance_usd": str(
                    self.paired_pnl
                    - self.config.rebalancing_cost_usd
                    * (self.counts.get("trade_completed", 0) + self.counts.get("trade_unhedged", 0))
                ),
                "fees_usd": str(self.fees_paid),
                "max_drawdown_fraction": str(self.drawdown),
                "stale_valuation_marks": stale,
                "valuation_complete": self.initialized and not stale,
                "liabilities_usd": "0",
                "unhedged": {a: str(q) for a, q in self.unhedged.items()},
                "balances": balances,
                "counts": self.counts,
                "rejections": self.rejections,
                "assumptions": self.config.model_dump(mode="json"),
            }
            if not self.initialized:
                for key in (
                    "nav_usd",
                    "initial_nav_usd",
                    "trading_pnl_usd",
                    "total_pnl_usd",
                    "realized_inventory_pnl_usd",
                    "unrealized_inventory_pnl_usd",
                    "paired_cashflow_pnl_usd",
                    "paired_pnl_after_assumed_rebalance_usd",
                    "max_drawdown_fraction",
                ):
                    result[key] = None
            return result
