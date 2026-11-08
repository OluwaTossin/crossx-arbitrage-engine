"""Read-only public spot adapters and explicit venue registry."""

from crossx.adapters.base import PublicAdapter
from crossx.adapters.binance import BinanceAdapter
from crossx.adapters.coinbase import CoinbaseAdapter
from crossx.adapters.cryptocom import CryptoComAdapter
from crossx.adapters.kraken import KrakenAdapter
from crossx.schemas import Exchange, Symbol

REGISTRY: dict[Exchange, type[PublicAdapter]] = {
    "kraken": KrakenAdapter,
    "coinbase": CoinbaseAdapter,
    "binance": BinanceAdapter,
    "cryptocom": CryptoComAdapter,
}


def build_adapters(
    exchanges: tuple[Exchange, ...], symbols: tuple[Symbol, ...]
) -> dict[Exchange, PublicAdapter]:
    return {name: REGISTRY[name](symbols) for name in exchanges}


__all__ = [
    "CoinbaseAdapter",
    "KrakenAdapter",
    "BinanceAdapter",
    "CryptoComAdapter",
    "build_adapters",
]
