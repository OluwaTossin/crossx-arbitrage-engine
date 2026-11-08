"""Validated, immutable canonical market observations; all money uses Decimal."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

Exchange = Literal["kraken", "coinbase", "binance", "cryptocom"]
Symbol = Literal["BTC/USD", "ETH/USD", "BTC/USDT", "ETH/USDT", "BTC/USDC", "ETH/USDC"]


def utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone aware")
    return value.astimezone(UTC)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Level(FrozenModel):
    price: Decimal
    quantity: Decimal

    @field_validator("price", "quantity")
    @classmethod
    def positive(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("level values must be finite and positive")
        return value


class OrderBook(FrozenModel):
    exchange: Exchange
    symbol: Symbol
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]
    observed_at: datetime
    received_at: datetime
    synchronized: bool = True
    market_id: str | None = None

    @property
    def quote_asset(self) -> str:
        return self.symbol.split("/")[1]

    @property
    def base_asset(self) -> str:
        return self.symbol.split("/")[0]

    _timestamps = field_validator("observed_at", "received_at")(utc_timestamp)

    @model_validator(mode="after")
    def valid_book(self) -> Self:
        if not self.bids or not self.asks:
            raise ValueError("both book sides must be nonempty")
        bids = [level.price for level in self.bids]
        asks = [level.price for level in self.asks]
        if bids != sorted(set(bids), reverse=True) or asks != sorted(set(asks)):
            raise ValueError("book levels must be sorted and unique")
        if bids[0] >= asks[0]:
            raise ValueError("crossed or locked book")
        return self


class FeeSchedule(FrozenModel):
    maker: Decimal | None = None
    taker: Decimal | None = None

    @field_validator("maker", "taker")
    @classmethod
    def valid_rate(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and (not value.is_finite() or value < 0 or value > 1):
            raise ValueError("fee fractions must be finite and between zero and one")
        return value


class Opportunity(FrozenModel):
    symbol: Symbol
    buy_exchange: Exchange
    sell_exchange: Exchange
    quantity: Decimal
    buy_vwap: Decimal
    sell_vwap: Decimal
    gross_profit: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    net_profit: Decimal
    observed_at: datetime

    buy_book_observed_at: datetime | None = None
    sell_book_observed_at: datetime | None = None
    buy_book_received_at: datetime | None = None
    sell_book_received_at: datetime | None = None
    buy_taker_rate: Decimal | None = None
    sell_taker_rate: Decimal | None = None
    detected_at: datetime | None = None

    _timestamp = field_validator("observed_at")(utc_timestamp)

    @field_validator("buy_taker_rate", "sell_taker_rate")
    @classmethod
    def provenance_rate(cls, value: Decimal | None) -> Decimal | None:
        return FeeSchedule.valid_rate(value)

    @field_validator(
        "buy_book_observed_at",
        "sell_book_observed_at",
        "buy_book_received_at",
        "sell_book_received_at",
        "detected_at",
    )
    @classmethod
    def optional_timestamp(cls, value: datetime | None) -> datetime | None:
        return utc_timestamp(value) if value is not None else None

    @model_validator(mode="after")
    def valid_opportunity(self) -> Self:
        if self.buy_exchange == self.sell_exchange:
            raise ValueError("opportunity must span exchanges")
        if min(self.quantity, self.buy_vwap, self.sell_vwap) <= 0:
            raise ValueError("quantity and execution prices must be positive")
        if min(self.buy_fee, self.sell_fee) < 0:
            raise ValueError("fees must be nonnegative")
        return self
