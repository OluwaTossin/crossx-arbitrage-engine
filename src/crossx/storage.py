"""Read-only database/volume telemetry and retention planning. Never deletes data."""

import argparse
import asyncio
import json
import logging
import os
import signal
import time

from prometheus_client import CollectorRegistry, Gauge, start_http_server

from crossx.config import Settings
from crossx.db import Database
from crossx.observability import configure_logging
from crossx.paper_metrics import PaperMetrics

logger = logging.getLogger(__name__)


async def export(settings: Settings, path: str, port: int) -> None:
    registry = CollectorRegistry()
    paper = PaperMetrics(registry)
    database_bytes = Gauge(
        "crossx_storage_database_bytes", "Database allocated bytes", registry=registry
    )
    book_bytes = Gauge(
        "crossx_storage_books_bytes", "Book table/index/TOAST bytes", registry=registry
    )
    free = Gauge(
        "crossx_storage_volume_free_bytes",
        "Free bytes on PostgreSQL volume filesystem",
        registry=registry,
    )
    size = Gauge(
        "crossx_storage_volume_size_bytes", "PostgreSQL volume filesystem size", registry=registry
    )
    budget = Gauge(
        "crossx_storage_budget_bytes",
        "Configured dataset budget, not a filesystem quota",
        registry=registry,
    )
    daily = Gauge(
        "crossx_storage_daily_book_budget_bytes", "Daily book-growth budget", registry=registry
    )
    up = Gauge("crossx_storage_up", "Last telemetry poll succeeded", registry=registry)
    last = Gauge(
        "crossx_storage_last_success_timestamp_seconds",
        "Last successful storage poll",
        registry=registry,
    )
    budget.set(settings.storage_budget_gib * 1024**3)
    daily.set(settings.daily_book_budget_gib * 1024**3)
    database = Database(settings.database_url)
    try:
        server, thread = start_http_server(port, registry=registry)
        try:
            while True:
                try:
                    async with asyncio.timeout(settings.persistence_timeout_seconds):
                        db, books = await database.storage_usage()
                        paper.update(await database.latest_paper_result())
                    usage = os.statvfs(path)
                    database_bytes.set(db)
                    book_bytes.set(books)
                    free.set(usage.f_bavail * usage.f_frsize)
                    size.set(usage.f_blocks * usage.f_frsize)
                    last.set(time.time())
                    up.set(1)
                except Exception as exc:
                    up.set(0)
                    # Never log the database URL or credentials.
                    logger.warning("storage_poll_failed", extra={"reason": type(exc).__name__})
                await asyncio.sleep(15)
        finally:
            # shutdown() waits for serve_forever; do not block the event loop.
            try:
                async with asyncio.timeout(2):
                    await asyncio.to_thread(server.shutdown)
            finally:
                server.server_close()
                thread.join(timeout=0.5)
                if thread.is_alive():
                    raise RuntimeError("Storage HTTP server did not terminate")
    finally:
        async with asyncio.timeout(settings.shutdown_timeout_seconds):
            await database.close()


async def serve(settings: Settings, path: str, port: int) -> None:
    """Translate process signals into one cancellation, allowing cleanup to finish."""
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(export(settings, path, port), name="storage-exporter")

    def stop() -> None:
        # Repeated SIGTERM must not cancel the cleanup that the first one began.
        if not task.cancelling():
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop)
    try:
        try:
            await task
        except asyncio.CancelledError:
            logger.info("storage_exporter_stopped")
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


async def plan(settings: Settings) -> None:
    database = Database(settings.database_url)
    try:
        async with asyncio.timeout(settings.persistence_timeout_seconds):
            db, books = await database.storage_usage()
            eligible = (
                await database.retention_plan(settings.retention_days)
                if settings.retention_days is not None
                else None
            )
        print(
            json.dumps(
                {
                    "mode": "read_only_plan",
                    "database_bytes": db,
                    "book_bytes": books,
                    "retention_days": settings.retention_days,
                    "rows_older_than_policy": eligible,
                    "automatic_deletion": False,
                    "sample_interval_seconds": settings.book_sample_interval_seconds,
                }
            )
        )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="Report retention eligibility only")
    parser.add_argument("--path", default="/pgdata")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    configure_logging()
    settings = Settings()
    asyncio.run(plan(settings) if args.plan else serve(settings, args.path, args.port))


if __name__ == "__main__":
    main()
