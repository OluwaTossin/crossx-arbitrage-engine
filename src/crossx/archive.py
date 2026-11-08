"""Read-only research export and checksum/schema verification. No deletion command."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path

from crossx.config import Settings
from crossx.db import Database
from crossx.schemas import OrderBook, utc_timestamp


def verify(directory: Path) -> int:
    manifest = json.loads((directory / "manifest.json").read_text())
    source = directory / "books.jsonl"
    digest = hashlib.sha256()
    count = 0
    previous = None
    with source.open("rb") as handle:
        for line in handle:
            digest.update(line)
            book = OrderBook.model_validate_json(line)
            if previous is not None and book.received_at < previous:
                raise ValueError("Archive receipt ordering invalid")
            previous = book.received_at
            count += 1
    if digest.hexdigest() != manifest["sha256"] or count != manifest["observations"]:
        raise ValueError("Archive checksum/count mismatch")
    return count


async def export(start: datetime, end: datetime, out: Path, dry_run: bool) -> dict[str, object]:
    start, end = utc_timestamp(start), utc_timestamp(end)
    if start >= end:
        raise ValueError("start must precede end")
    db = Database(Settings().database_url)
    digest = hashlib.sha256()
    count = 0
    handle = None
    try:
        if not dry_run:
            out.mkdir(parents=True, exist_ok=False)
            handle = (out / "books.jsonl").open("xb")
        async for book in db.iter_books(start, end):
            encoded = (book.model_dump_json() + "\n").encode()
            digest.update(encoded)
            count += 1
            if handle:
                handle.write(encoded)
        result = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "observations": count,
            "sha256": digest.hexdigest(),
            "dry_run": dry_run,
            "automatic_deletion": False,
            "ordering": "received_at,id",
            "format": "canonical-jsonl-v1",
        }
        if handle:
            handle.close()
            handle = None
            (out / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
            verify(out)
        return result
    finally:
        if handle:
            handle.close()
        await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps({"verified_observations": verify(args.verify)}))
    else:
        if not args.start or not args.end or not args.out:
            parser.error("export requires --start, --end and --out")
        print(
            json.dumps(
                asyncio.run(
                    export(
                        datetime.fromisoformat(args.start),
                        datetime.fromisoformat(args.end),
                        args.out,
                        args.dry_run,
                    )
                )
            )
        )


if __name__ == "__main__":
    main()
