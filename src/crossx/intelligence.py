"""Fee-aware, full-depth estimates for prefunded spot paper trading.

An opportunity requires inventory on the sell venue and cash on the buy venue.
The quantities are research assumptions, not an allocation recommendation for a
£500–£1,500 portfolio. Funding, GBP conversion, rebalancing, transfers, adverse
selection and execution latency are not included; estimated profit is not a
claim of executable or realised profit. No orders are submitted by this module.
"""

import math
from collections import Counter
from datetime import datetime
from decimal import Decimal, localcontext
from itertools import permutations

from crossx.schemas import Exchange, FeeSchedule, Level, Opportunity, OrderBook


def _vwap(levels: tuple[Level, ...], quantity: Decimal) -> Decimal | None:
    """Consume displayed depth without extrapolating beyond available liquidity."""
    remaining = quantity
    notional = Decimal(0)
    for level in levels:
        consumed = min(remaining, level.quantity)
        notional += consumed * level.price
        remaining -= consumed
        if remaining == 0:
            return notional / quantity
    return None


def detect_opportunities(
    books: list[OrderBook],
    fees: dict[Exchange, FeeSchedule],
    quantity: Decimal,
    now: datetime,
    max_age_seconds: float = 5,
    max_skew_seconds: float = 2,
    min_net_profit: Decimal = Decimal("0"),
) -> list[Opportunity]:
    """Return positive net estimates, rejecting incomplete or unsafe observations.

    Fees are quote-currency taker estimates on each leg's full notional. Unknown
    fees fail closed. Both venue clocks (event and local receive time) must be
    fresh, with no future timestamps; inter-venue event skew is bounded as well.
    Exactly one book per exchange/symbol is accepted to prevent ambiguous replay
    histories from being mistaken for current cross-venue observations.
    """
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("quantity must be positive and finite")
    if not min_net_profit.is_finite() or min_net_profit < 0:
        raise ValueError("min_net_profit must be nonnegative and finite")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if any(not math.isfinite(value) or value < 0 for value in (max_age_seconds, max_skew_seconds)):
        raise ValueError("age and skew limits must be nonnegative and finite")

    counts = Counter((book.exchange, book.symbol) for book in books)
    valid = []
    for book in books:
        ages = [
            (now - timestamp).total_seconds() for timestamp in (book.observed_at, book.received_at)
        ]
        if (
            book.synchronized
            and counts[(book.exchange, book.symbol)] == 1
            and all(0 <= age <= max_age_seconds for age in ages)
        ):
            valid.append(book)

    results = []
    # Keep financial arithmetic Decimal throughout, independent of caller context.
    with localcontext() as context:
        context.prec = 50
        for buy, sell in permutations(valid, 2):
            if buy.exchange == sell.exchange or buy.symbol != sell.symbol:
                continue
            if abs((buy.observed_at - sell.observed_at).total_seconds()) > max_skew_seconds:
                continue
            buy_schedule = fees.get(buy.exchange)
            sell_schedule = fees.get(sell.exchange)
            buy_rate = buy_schedule.taker if buy_schedule else None
            sell_rate = sell_schedule.taker if sell_schedule else None
            if buy_rate is None or sell_rate is None:
                continue
            buy_vwap = _vwap(buy.asks, quantity)
            sell_vwap = _vwap(sell.bids, quantity)
            if buy_vwap is None or sell_vwap is None:
                continue
            buy_notional = buy_vwap * quantity
            sell_notional = sell_vwap * quantity
            gross = sell_notional - buy_notional
            buy_fee = buy_notional * buy_rate
            sell_fee = sell_notional * sell_rate
            net = gross - buy_fee - sell_fee
            if net <= min_net_profit:
                continue
            results.append(
                Opportunity(
                    symbol=buy.symbol,
                    buy_exchange=buy.exchange,
                    sell_exchange=sell.exchange,
                    quantity=quantity,
                    buy_vwap=buy_vwap,
                    sell_vwap=sell_vwap,
                    gross_profit=gross,
                    buy_fee=buy_fee,
                    sell_fee=sell_fee,
                    net_profit=net,
                    observed_at=max(buy.observed_at, sell.observed_at),
                    buy_book_observed_at=buy.observed_at,
                    sell_book_observed_at=sell.observed_at,
                    buy_book_received_at=buy.received_at,
                    sell_book_received_at=sell.received_at,
                    buy_taker_rate=buy_rate,
                    sell_taker_rate=sell_rate,
                    detected_at=now,
                )
            )
    return results
