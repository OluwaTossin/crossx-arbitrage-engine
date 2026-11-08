"""Bounded public-only probe; no authentication or order submission paths."""

from __future__ import annotations

import argparse
import asyncio
import json

from crossx.adapters import CoinbaseAdapter, KrakenAdapter


async def probe(name: str, timeout: float, duration: float) -> bool:
    adapter = {"kraken": KrakenAdapter, "coinbase": CoinbaseAdapter}[name](("BTC/USD", "ETH/USD"))
    stream = adapter.stream()
    seen = set()
    counts: dict[str, int] = {}
    generation = None
    started = asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(timeout):
            async for book in stream:
                if generation is None:
                    generation = adapter.generation
                    started = asyncio.get_running_loop().time()
                if adapter.generation != generation:
                    raise RuntimeError(f"Stream reset during sustained probe: {adapter.last_error}")
                counts[book.symbol] = counts.get(book.symbol, 0) + 1
                seen.add(book.symbol)
                if counts[book.symbol] == 1:
                    print(
                        json.dumps(
                            {
                                "exchange": name,
                                "symbol": book.symbol,
                                "bids": len(book.bids),
                                "asks": len(book.asks),
                                "observed_at": book.observed_at.isoformat(),
                                "synchronized": book.synchronized,
                            }
                        ),
                        flush=True,
                    )
                if (
                    len(seen) == 2
                    and min(counts.values()) >= 2
                    and asyncio.get_running_loop().time() - started >= duration
                ):
                    print(
                        json.dumps(
                            {
                                "exchange": name,
                                "result": "passed" if adapter.reconnect_count == 0 else "failed",
                                "observations": counts,
                                "duration_seconds": duration,
                                "reconnects": adapter.reconnect_count,
                                "integrity_errors": adapter.integrity_error_count,
                            }
                        ),
                        flush=True,
                    )
                    return adapter.reconnect_count == 0
    except TimeoutError:
        print(
            json.dumps(
                {
                    "exchange": name,
                    "error": "timeout",
                    "symbols_seen": sorted(seen),
                    "reconnects": adapter.reconnect_count,
                    "last_error": adapter.last_error,
                }
            ),
            flush=True,
        )
    finally:
        await stream.aclose()
    return False


async def main(timeout: float, duration: float) -> None:
    results = await asyncio.gather(
        probe("kraken", timeout, duration), probe("coinbase", timeout, duration)
    )
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument(
        "--duration",
        type=float,
        default=0,
        help="Minimum sustained observation seconds; timeout must exceed this",
    )
    args = parser.parse_args()
    if args.duration < 0 or args.timeout <= args.duration:
        parser.error("Require 0 <= duration < timeout")
    asyncio.run(main(args.timeout, args.duration))
