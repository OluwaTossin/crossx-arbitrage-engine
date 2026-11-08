"""Regression coverage from independent exchange-integrity review."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from crossx.adapters import CoinbaseAdapter


class SnapshotSocket:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, message):
        pass

    async def recv(self):
        fixture = Path(__file__).parent / "fixtures/coinbase_replay.json"
        return json.dumps(json.loads(fixture.read_text())[0])


@pytest.mark.asyncio
async def test_closing_stream_invalidates_synchronized_generation():
    adapter = CoinbaseAdapter(("BTC/USD",))
    with patch("crossx.adapters.base.websockets.connect", return_value=SnapshotSocket()):
        stream = adapter.stream()
        await anext(stream)
        assert adapter.synchronized_symbols == {"BTC/USD"}
        generation = adapter.generation
        await stream.aclose()
    assert adapter.synchronized_symbols == set()
    assert adapter.books == {}
    assert adapter.generation > generation
