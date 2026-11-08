"""Opt-in bounded forward paper evaluation of persisted public observations.

Runs only when explicitly invoked. Receipt windows lag wall clock to allow
commits; a late insert behind a closed window fails the session, never silently
rewinds. No exchange transport, credentials, transfers or orders exist here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select

from crossx.config import Settings
from crossx.db import Database, books
from crossx.paper import PaperConfig, Simulator


async def late_watermark(database: Database, last_id: int, cursor: datetime) -> tuple[int, int]:
    async with database.engine.connect() as connection:
        async with connection.begin():
            # A statement-level snapshot checks late insertions and max id consistently.
            row = (
                await connection.execute(
                    select(
                        func.coalesce(func.max(books.c.id), 0),
                        func.count().filter(books.c.id > last_id, books.c.received_at < cursor),
                    )
                )
            ).one()
            return int(row[0]), int(row[1])


async def run(args: argparse.Namespace) -> None:
    config = PaperConfig.model_validate_json(Path(args.config).read_text())
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    db = Database(Settings().database_url)
    session = str(uuid4())
    events: list[dict[str, object]] = []
    cursor = datetime.now(UTC) - timedelta(seconds=args.commit_lag)
    last_id, _ = await late_watermark(db, 0, cursor)
    started = time.monotonic()
    observations = 0
    with (output / "events.jsonl").open("x") as evidence:

        def emit(event: dict[str, object]) -> None:
            events.append(event)
            evidence.write(json.dumps(event, sort_keys=True) + "\n")
            evidence.flush()

        sim = Simulator(config, emit)
        await db.start_session(
            session,
            config.model_dump(mode="json"),
            {
                "source_commit": args.source_commit,
                "mode": "forward_receipt_windows",
                "commit_lag_seconds": args.commit_lag,
                "start": cursor.isoformat(),
            },
        )
        try:
            async with asyncio.timeout(args.duration + 30):
                while time.monotonic() - started < args.duration:
                    end = datetime.now(UTC) - timedelta(seconds=args.commit_lag)
                    watermark, late = await late_watermark(db, last_id, cursor)
                    if late:
                        raise RuntimeError(
                            f"{late} late inserts behind replay cursor; evidence incomplete"
                        )
                    source = db.iter_books(cursor, end)
                    try:
                        async for book in source:
                            observations += 1
                            sim.ingest(book)
                            if len(events) >= 128:
                                await db.paper_checkpoint(session, events)
                                events.clear()
                    finally:
                        await source.aclose()
                    sim.advance(end)
                    cursor = end
                    last_id = watermark
                    if events:
                        await db.paper_checkpoint(session, events)
                        events.clear()
                    await asyncio.sleep(
                        min(1, max(0, args.duration - (time.monotonic() - started)))
                    )
            result = sim.finish()
            result["forward_observations"] = observations
            result["actual_elapsed_seconds"] = time.monotonic() - started
            await db.paper_checkpoint(session, events, result)
            (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        except BaseException as exc:
            (output / "incomplete.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "status": "incomplete",
                        "reason": type(exc).__name__,
                        "actual_elapsed_seconds": time.monotonic() - started,
                        "observations": observations,
                    }
                )
                + "\n"
            )
            raise
        finally:
            await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--commit-lag", type=int, default=2)
    args = parser.parse_args()
    if args.duration <= 0 or not 0 <= args.commit_lag <= 60:
        parser.error("positive duration and commit lag 0..60 required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
