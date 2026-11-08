"""Bounded three-capital forward verification against an explicit isolated database."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from sqlalchemy import text

from crossx.db import Database


async def main(args: argparse.Namespace) -> None:
    root = Path(args.out)
    root.mkdir(parents=True, exist_ok=False)
    db = Database(args.database_url)
    env = {**os.environ, "CROSSX_DATABASE_URL": args.database_url}

    async def stats() -> dict[str, int]:
        async with db.engine.connect() as c:
            row = (
                (
                    await c.execute(
                        text("""SELECT pg_database_size(current_database()) AS database,
                pg_total_relation_size('book_observations') AS books,
                pg_total_relation_size('paper_events') AS events,
                pg_total_relation_size('paper_sessions') AS sessions,
                pg_total_relation_size('paper_source_windows') AS windows""")
                    )
                )
                .mappings()
                .one()
            )
            return {k: int(v) for k, v in row.items()}

    async def run(capital: int) -> None:
        command = [
            ".venv/bin/python",
            "-m",
            "crossx.forward",
            "--config",
            f"config/readiness-gbp-{capital}.json",
            "--duration",
            str(args.duration),
            "--source-commit",
            args.source_commit,
            "--out",
            str(root / str(capital)),
        ]
        child = await asyncio.create_subprocess_exec(
            *command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), args.duration + 120)
        (root / f"process-{capital}.json").write_text(
            json.dumps(
                {
                    "exit": child.returncode,
                    "stdout": stdout.decode(),
                    "stderr": stderr.decode(),
                    "command": command,
                },
                indent=2,
            )
            + "\n"
        )
        if child.returncode:
            raise RuntimeError(f"forward scenario {capital} failed")

    try:
        before = await stats()
        started = time.monotonic()
        await asyncio.gather(*(run(c) for c in (500, 1000, 1500)))
        after = await stats()
        (root / "storage.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "after": after,
                    "actual_seconds": time.monotonic() - started,
                    "note": (
                        "Three concurrent sessions; collection also running. "
                        "Allocated bytes are page-granular."
                    ),
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--duration", type=int, default=60)
    asyncio.run(main(parser.parse_args()))
