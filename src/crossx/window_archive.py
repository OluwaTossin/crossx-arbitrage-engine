"""Lossless closed-window export; verifies source sequence, original clocks and SHA256."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path

from crossx.config import Settings
from crossx.db import Database
from crossx.forward_store import ForwardStore, Window, rows_digest
from crossx.recovery import canonical
from crossx.schemas import OrderBook


def verify(path: Path) -> Window:
    meta = json.loads((path / "manifest.json").read_text())
    rows = [json.loads(line) for line in (path / "source.jsonl").read_text().splitlines()]
    if len(rows) != meta["rows"] or rows_digest(rows) != meta["input_sha256"]:
        raise ValueError("Window archive checksum/count mismatch")
    seq = [r["sequence"] for r in rows]
    if seq != sorted(set(seq)) or any(not meta["start"] < x <= meta["end"] for x in seq):
        raise ValueError("Window archive source ordering invalid")
    for row in rows:
        OrderBook.model_validate(row["book"])
    return Window(
        meta["version"],
        meta["start"],
        meta["end"],
        datetime.fromisoformat(meta["closed_at"]),
        rows,
        meta["input_sha256"],
    )


async def export(session: str, version: int, path: Path, dry_run: bool) -> dict[str, object]:
    db = Database(Settings().database_url)
    try:
        async with asyncio.timeout(Settings().persistence_timeout_seconds):
            window = await ForwardStore(db).read_window(session, version)
        result = {
            "session": session,
            "version": version,
            "start": window.start,
            "end": window.end,
            "closed_at": window.closed_at.isoformat(),
            "rows": len(window.rows),
            "input_sha256": window.sha256,
            "dry_run": dry_run,
            "automatic_deletion": False,
        }
        if not dry_run:
            path.mkdir(parents=True, exist_ok=False)
            with (path / "source.jsonl").open("xb") as handle:
                for row in window.rows:
                    handle.write(canonical(row) + b"\n")
            (path / "manifest.json").write_bytes(canonical(result) + b"\n")
            verify(path)
        return result
    finally:
        await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--session")
    p.add_argument("--version", type=int)
    p.add_argument("--out", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verify", type=Path)
    a = p.parse_args()
    if a.verify:
        w = verify(a.verify)
        print(json.dumps({"verified_rows": len(w.rows), "sha256": w.sha256}))
    else:
        if not a.session or a.version is None or not a.out:
            p.error("session, version, out required")
        print(json.dumps(asyncio.run(export(a.session, a.version, a.out, a.dry_run)), indent=2))


if __name__ == "__main__":
    main()
