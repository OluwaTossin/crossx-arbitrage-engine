import asyncio

import pytest

from crossx import capacity
from crossx.config import Settings


def test_capacity_budget_and_headroom():
    r = capacity.projection(350 * 1024**3, 14 * 86400, 23.22, 5)
    assert not r["ready"]
    assert r["required_bytes"] > 350 * 1024**3
    assert capacity.projection(10 * 1024**3, 900, 23.22, 5)["ready"]


async def test_critical_disk_stops_without_deletion(monkeypatch):
    monkeypatch.setattr(capacity, "free_bytes", lambda path: 100)
    stop = asyncio.Event()
    await capacity.guard(Settings(disk_guard_path="/disposable"), stop)
    assert stop.is_set()
    with pytest.raises(capacity.CapacityError):
        capacity.preflight(Settings(disk_guard_path="/disposable"))


async def test_missing_disk_telemetry_fails_closed(monkeypatch):
    def broken(path):
        raise OSError("volume unavailable")

    monkeypatch.setattr(capacity, "free_bytes", broken)
    stop = asyncio.Event()
    await capacity.guard(Settings(disk_guard_path="/disposable"), stop)
    assert stop.is_set()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_capacity_invalid_inputs_fail_closed(value):
    with pytest.raises(ValueError):
        capacity.projection(100, value, 23.22, 5)
