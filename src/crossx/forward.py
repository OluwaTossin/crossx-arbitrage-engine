"""Opt-in recoverable paper evaluation over immutable, transactionally closed inputs."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from crossx.config import Settings
from crossx.db import Database
from crossx.forward_store import ForwardStore, RecoveryConflict, Window
from crossx.paper import PaperConfig, Simulator
from crossx.recovery import digest, restore, snapshot
from crossx.schemas import OrderBook


def process_window(sim: Simulator, window: Window) -> None:
    if sim.now is not None and window.closed_at < sim.now:
        raise RecoveryConflict("Source closure clock regressed")
    sim.advance(window.closed_at)
    for row in window.rows:
        book = OrderBook.model_validate(row["book"])
        if book.received_at > window.closed_at:
            sim.event("source_rejected", reason="future_receipt", source_sequence=row["sequence"])
            continue
        sim.ingest(book, available_at=window.closed_at)


async def run(args: argparse.Namespace) -> None:
    db = Database(Settings().database_url)
    try:
        await _run(args, db)
    finally:
        await db.close()


async def _run(args: argparse.Namespace, db: Database) -> None:
    config = PaperConfig.model_validate_json(Path(args.config).read_text())
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=False)
    store = ForwardStore(db)
    session = getattr(args, "resume", None) or str(uuid4())
    events: list[dict[str, object]] = []
    started = time.monotonic()
    attempt = str(uuid4())
    attempt_recorded = False
    settings = Settings()
    with (output / "events.jsonl").open("x") as evidence:
        try:
            async with asyncio.timeout(settings.persistence_timeout_seconds):
                if getattr(args, "resume", None):
                    checkpoint = await store.load(session)
                    if checkpoint["provenance"].get("source_commit") != args.source_commit:
                        raise RecoveryConflict(
                            "Resume requires the original verified executable source commit"
                        )
                    sim = restore(checkpoint["state"]["simulator"], events.append)
                    if digest(config.model_dump(mode="json")) != digest(
                        sim.config.model_dump(mode="json")
                    ):
                        raise RecoveryConflict(
                            "Resume configuration differs; no portfolio reset allowed"
                        )
                    if checkpoint["status"] == "complete":
                        raise RecoveryConflict("Completed session cannot resume")
                    recovery = "successfully_resumed"
                else:
                    sim = Simulator(config, events.append)
                    checkpoint = await store.begin(
                        session,
                        snapshot(sim),
                        {"source_commit": args.source_commit},
                        getattr(args, "from_start", False),
                    )
                    recovery = "new_session"
            async with asyncio.timeout(settings.persistence_timeout_seconds):
                await store.attempt_start(session, attempt)
                attempt_recorded = True
            version = checkpoint["version"]
            state = checkpoint["state"]
            (output / "session.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "recovery": recovery,
                        "starting_checkpoint": version,
                        "starting_cursor": state["cursor"],
                        "provenance": checkpoint["provenance"],
                    },
                    indent=2,
                )
                + "\n"
            )
            while time.monotonic() - started < args.duration:
                async with asyncio.timeout(settings.persistence_timeout_seconds):
                    window = await store.window(
                        session, version + 1, state["cursor"], getattr(args, "window_size", 256)
                    )
                process_window(sim, window)
                state = {
                    "simulator": snapshot(sim),
                    "cursor": window.end,
                    "observations": state["observations"] + len(window.rows),
                    "windows": state["windows"] + 1,
                }
                async with asyncio.timeout(settings.persistence_timeout_seconds):
                    version = await store.checkpoint(session, version, state, events, "recoverable")
                # Database is canonical. Local attempt evidence contains only acknowledged commits.
                for event in events:
                    evidence.write(json.dumps(event, sort_keys=True) + "\n")
                evidence.flush()
                events.clear()
                if len(window.rows) < getattr(args, "window_size", 256):
                    await asyncio.sleep(
                        min(1, max(0, args.duration - (time.monotonic() - started)))
                    )
            result = sim.finish(drain=False)
            result.update(
                {
                    "session_id": session,
                    "recovery": recovery,
                    "source_cursor": state["cursor"],
                    "source_windows": state["windows"],
                    "forward_observations": state["observations"],
                    "actual_attempt_seconds": time.monotonic() - started,
                    "status": "paused_recoverable",
                    "pending_legs": len(sim.pending),
                }
            )
            async with asyncio.timeout(settings.persistence_timeout_seconds):
                version = await store.checkpoint(session, version, state, events, "paused", result)
            async with asyncio.timeout(settings.persistence_timeout_seconds):
                await store.attempt_end(attempt, "paused")
            result["checkpoint_version"] = version
            (output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        except BaseException as exc:
            # Never checkpoint partially mutated in-memory state after failure.
            if attempt_recorded:
                try:
                    async with asyncio.timeout(settings.persistence_timeout_seconds):
                        await store.attempt_end(attempt, "interrupted", type(exc).__name__)
                except Exception:
                    pass  # Missing terminal audit stays explicit; never invent its end time.
            (output / "interrupted.json").write_text(
                json.dumps(
                    {
                        "session_id": session,
                        "classification": "interrupted_check_last_durable_checkpoint",
                        "reason": type(exc).__name__,
                        "actual_attempt_seconds": time.monotonic() - started,
                    }
                )
                + "\n"
            )
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument(
        "--resume", help="Existing recoverable session id; exact stored configuration required"
    )
    parser.add_argument(
        "--from-start", action="store_true", help="Include all protocol-certified source rows"
    )
    parser.add_argument("--window-size", type=int, default=256)
    args = parser.parse_args()
    if args.duration <= 0 or not 1 <= args.window_size <= 1024:
        parser.error("positive duration and window size 1..1024 required")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
