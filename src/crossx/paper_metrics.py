"""Bounded labels for latest COMPLETED persisted paper session, not live trading."""

from typing import Any

from prometheus_client import CollectorRegistry, Gauge

from crossx.paper import VENUES


class PaperMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.available = Gauge(
            "crossx_paper_result_available", "Completed paper session exists", registry=registry
        )
        self.count = Gauge(
            "crossx_paper_events",
            "Latest completed session event counts",
            ["stage"],
            registry=registry,
        )
        self.pnl = Gauge(
            "crossx_paper_total_pnl_usd",
            "Latest session P&L including conversion/funding assumptions",
            registry=registry,
        )
        self.drawdown = Gauge(
            "crossx_paper_drawdown_fraction", "Latest session maximum drawdown", registry=registry
        )
        self.balance = Gauge(
            "crossx_paper_balance",
            "Latest session simulated balances",
            ["exchange", "asset"],
            registry=registry,
        )
        self.unhedged = Gauge(
            "crossx_paper_unhedged_quantity",
            "Latest session residual base exposure",
            ["asset"],
            registry=registry,
        )
        self.utilization = Gauge(
            "crossx_paper_capital_utilization",
            "Latest session ending inventory/NAV",
            registry=registry,
        )
        self.reject = Gauge(
            "crossx_paper_rejections", "Latest session reasons", ["reason"], registry=registry
        )

    def update(self, result: dict[str, Any] | None) -> None:
        self.available.set(result is not None and result.get("initialized", False))
        if result is None or not result.get("initialized", False):
            self.pnl.set(float("nan"))
            self.drawdown.set(float("nan"))
            return
        self.pnl.set(float(result["total_pnl_usd"]))
        self.drawdown.set(float(result["max_drawdown_fraction"]))
        for stage in (
            "raw_spread",
            "theoretical",
            "capital_feasible",
            "leg",
            "trade_completed",
            "trade_unhedged",
            "trade_failed",
            "rejected",
            "partial_fill",
            "failed_leg",
        ):
            self.count.labels(stage).set(result["counts"].get(stage, 0))
        for venue in VENUES:
            wallet = result["balances"][venue] if venue in result["balances"] else None
            self.balance.labels(venue, "USD").set(float(wallet["cash_usd"]) if wallet else 0)
            for asset in ("BTC", "ETH"):
                self.balance.labels(venue, asset).set(
                    float(wallet["inventory"][asset]["quantity"]) if wallet else 0
                )
        for asset in ("BTC", "ETH"):
            self.unhedged.labels(asset).set(float(result["unhedged"][asset]))
        for reason in (
            "unknown_fee",
            "unknown_market_rules",
            "insufficient_capital",
            "inventory_depleted",
            "loss_limit",
            "exchange_exposure",
            "inventory_concentration",
            "stale_or_unsynchronized",
            "clock_skew",
            "order_constraints",
            "insufficient_liquidity",
            "net_threshold",
            "trade_limit",
            "quantity_increment",
            "quote_conversion_unavailable",
        ):
            self.reject.labels(reason).set(result.get("rejections", {}).get(reason, 0))
        nav = float(result["nav_usd"])
        cash = sum(float(w["cash_usd"]) for w in result["balances"].values())
        self.utilization.set((nav - cash) / nav if nav > 0 else 0)
