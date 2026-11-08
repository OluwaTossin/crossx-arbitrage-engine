"""Read-only disk preflight and fail-closed local collection guard. Never deletes data."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os

from crossx import observability as metrics
from crossx.config import Settings


class CapacityError(RuntimeError):
    pass


def free_bytes(path: str) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def projection(
    free: int, seconds: float, daily_gb: float, headroom_gib: float, safety_factor: float = 1.3
) -> dict[str, object]:
    if (
        not all(math.isfinite(x) for x in (free, seconds, daily_gb, headroom_gib, safety_factor))
        or min(free, seconds, daily_gb, headroom_gib) < 0
        or safety_factor < 1
    ):
        raise ValueError("Invalid capacity assumptions")
    required = daily_gb * 1_000_000_000 * seconds / 86400 * safety_factor + headroom_gib * 1024**3
    return {
        "free_bytes": free,
        "required_bytes": int(required),
        "duration_seconds": seconds,
        "daily_growth_gb_assumption": daily_gb,
        "headroom_gib": headroom_gib,
        "safety_factor": safety_factor,
        "ready": free >= required,
        "automatic_deletion": False,
    }


def preflight(settings: Settings) -> dict[str, object] | None:
    if not settings.disk_guard_path:
        return None
    result = projection(
        free_bytes(settings.disk_guard_path),
        settings.capacity_run_seconds,
        settings.capacity_daily_gb,
        settings.disk_headroom_gib,
        settings.capacity_safety_factor,
    )
    if not result["ready"]:
        raise CapacityError("Insufficient disk capacity; preserve evidence, stop collection")
    return result


async def guard(settings: Settings, stop: asyncio.Event) -> None:
    assert settings.disk_guard_path is not None
    while not stop.is_set():
        try:
            available = free_bytes(settings.disk_guard_path)
            metrics.DISK_FREE.set(available)
            blocked = available < settings.disk_critical_gib * 1024**3
        except OSError:
            blocked = True  # Missing telemetry cannot authorize continued collection.
        metrics.DISK_BLOCKED.set(blocked)
        if blocked:
            stop.set()
            return
        await asyncio.sleep(1)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--path", required=True)
    p.add_argument("--days", type=float, default=14)
    p.add_argument("--daily-gb", type=float, default=23.22)
    p.add_argument("--headroom-gib", type=float, default=5)
    a = p.parse_args()
    result = projection(free_bytes(a.path), a.days * 86400, a.daily_gb, a.headroom_gib)
    print(json.dumps(result, indent=2))
    if not result["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
