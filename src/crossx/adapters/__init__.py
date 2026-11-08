"""Credential-free, read-only spot market adapters."""

from crossx.adapters.coinbase import CoinbaseAdapter
from crossx.adapters.kraken import KrakenAdapter

__all__ = ["CoinbaseAdapter", "KrakenAdapter"]
