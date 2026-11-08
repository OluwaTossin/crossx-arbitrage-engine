"""Streaming deterministic replay/report CLI. No exchange transport is imported."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from crossx.config import Settings
from crossx.db import Database
from crossx.paper import PaperConfig, Simulator
from crossx.schemas import OrderBook, utc_timestamp


async def file_books(path: Path) -> AsyncGenerator[OrderBook, None]:
    with path.open() as source:
        for line in source:
            yield OrderBook.model_validate_json(line)


async def run(args: argparse.Namespace) -> None:
    config = PaperConfig.model_validate_json(Path(args.config).read_text())
    database = Database(Settings().database_url) if args.database else None
    session = str(uuid4())
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)  # Never overwrite research evidence.
    provenance = {
        "source_commit": args.source_commit,
        "receipt_start": args.start,
        "receipt_end": args.end,
        "source_file": args.input,
        "ordering": "received_at,id",
        "method": "displayed-depth paper simulation; no queue priority or impact proof",
    }
    pending: list[dict[str, object]] = []
    digest = hashlib.sha256()
    count = 0
    with (output / "events.jsonl").open("x") as evidence:

        def emit(event: dict[str, object]) -> None:
            encoded = json.dumps(event, sort_keys=True) + "\n"
            evidence.write(encoded)
            pending.append(event)

        simulator = Simulator(config, emit)
        source = (
            database.iter_books(
                utc_timestamp(datetime.fromisoformat(args.start)),
                utc_timestamp(datetime.fromisoformat(args.end)),
            )
            if database
            else file_books(Path(args.input))
        )
        try:
            if database:
                await database.start_session(session, config.model_dump(mode="json"), provenance)
            async for book in source:
                digest.update((book.model_dump_json() + "\n").encode())
                count += 1
                simulator.ingest(book)
                if len(pending) >= 128:
                    if database:
                        await database.paper_checkpoint(session, pending)
                    pending.clear()
            result = simulator.finish()
            provenance.update({"input_sha256": digest.hexdigest(), "input_observations": count})
            result.update({"provenance": provenance})
            if database:
                await database.paper_checkpoint(session, pending, result)
            pending.clear()
            (output / "report.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        finally:
            await source.aclose()
            if database:
                await database.close()
    manifest = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.is_file()
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", help="Canonical book JSONL, ordered by receipt")
    source.add_argument("--database", action="store_true")
    parser.add_argument("--start", help="Inclusive receipt timestamp for database replay")
    parser.add_argument("--end", help="Exclusive receipt timestamp for database replay")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    if args.database and (not args.start or not args.end):
        parser.error("database replay requires --start and --end")
    if args.database and datetime.fromisoformat(args.start) >= datetime.fromisoformat(args.end):
        parser.error("start must precede end")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
