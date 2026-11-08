"""Atomic simulator/source checkpoints over transactionally closed input windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from crossx.db import Database, books, paper_events, paper_sessions, source_windows
from crossx.recovery import digest

SOURCE_LOCK = 728406310427


class RecoveryConflict(RuntimeError):
    """Another worker/checkpoint won; stop rather than duplicate settlement."""


@dataclass(frozen=True)
class Window:
    version: int
    start: int
    end: int
    closed_at: datetime
    rows: list[dict[str, Any]]
    sha256: str


def rows_digest(rows: list[dict[str, Any]]) -> str:
    return digest(rows)


async def verify_protocol(connection: AsyncConnection) -> None:
    enabled = await connection.scalar(
        text("""
      SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgenabled = 'O'
        AND tgname IN ('crossx_order_insert','crossx_guard_frozen_book','crossx_guard_window')
    """)
    )
    if enabled != 3:
        raise RecoveryConflict("Source ordering/immutability triggers are not enabled")


class ForwardStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def begin(
        self, session: str, state: dict[str, Any], provenance: dict[str, Any], from_start: bool
    ) -> dict[str, Any]:
        async with self.database.engine.begin() as c:
            await verify_protocol(c)
            await c.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SOURCE_LOCK})
            cursor = (
                0
                if from_start
                else int(await c.scalar(select(func.max(books.c.source_sequence))) or 0)
            )
            legacy = int(
                await c.scalar(select(func.count()).where(books.c.source_sequence.is_(None))) or 0
            )
            data = {"simulator": state, "cursor": cursor, "observations": 0, "windows": 0}
            provenance = {
                **provenance,
                "initial_source_cursor": cursor,
                "legacy_uncertified_rows": legacy,
                "mode": "transactionally_closed_windows_v1",
            }
            await c.execute(
                paper_sessions.insert().values(
                    id=session,
                    created_at=datetime.now(UTC),
                    config=state["config"],
                    provenance=provenance,
                    recovery_state=data,
                    checkpoint_version=0,
                    source_cursor=cursor,
                    status="recoverable",
                    checkpoint_digest=digest(data),
                    checkpoint_at=datetime.now(UTC),
                )
            )
        return {"version": 0, "state": data, "status": "recoverable", "provenance": provenance}

    async def load(self, session: str) -> dict[str, Any]:
        async with self.database.engine.connect() as c:
            row = (
                (await c.execute(select(paper_sessions).where(paper_sessions.c.id == session)))
                .mappings()
                .one()
            )
            state = row["recovery_state"]
            if not state or row["status"] == "legacy":
                raise RecoveryConflict(
                    "Session has no recoverable checkpoint; no portfolio reset allowed"
                )
            # Bind recovered state separately from the whole checkpoint request digest.
            if digest(state) != row["provenance"].get("state_digest", row["checkpoint_digest"]):
                raise RecoveryConflict("Recovery state checksum mismatch")
            if int(state["cursor"]) != row["source_cursor"]:
                raise RecoveryConflict("Source/state cursor mismatch")
            return {
                "version": int(row["checkpoint_version"]),
                "state": dict(state),
                "status": row["status"],
                "provenance": dict(row["provenance"]),
            }

    async def window(self, session: str, version: int, cursor: int, limit: int = 256) -> Window:
        if not 1 <= limit <= 1024:
            raise ValueError("window size must be 1..1024")
        async with self.database.engine.begin() as c:
            # Same lock as INSERT trigger. All earlier writers finish before closure;
            # later writers cannot allocate a lower committed source sequence.
            await verify_protocol(c)
            await c.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SOURCE_LOCK})
            existing = (
                (
                    await c.execute(
                        select(source_windows).where(
                            source_windows.c.session_id == session,
                            source_windows.c.version == version,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["start_sequence"] != cursor:
                    raise RecoveryConflict("Window cursor mismatch")
                end = int(existing["end_sequence"])
                closed = existing["closed_at"]
            else:
                end = int(
                    await c.scalar(
                        select(books.c.source_sequence)
                        .where(books.c.source_sequence > cursor)
                        .order_by(books.c.source_sequence)
                        .offset(limit - 1)
                        .limit(1)
                    )
                    or 0
                )
                if not end:
                    end = int(await c.scalar(select(func.max(books.c.source_sequence))) or cursor)
                end = max(end, cursor)
                closed = await c.scalar(select(func.clock_timestamp()))
                assert isinstance(closed, datetime)
            raw = (
                (
                    await c.execute(
                        select(books.c.source_sequence, books.c.payload)
                        .where(books.c.source_sequence > cursor, books.c.source_sequence <= end)
                        .order_by(books.c.source_sequence)
                    )
                )
                .mappings()
                .all()
            )
            rows = [
                {"sequence": int(r["source_sequence"]), "book": dict(r["payload"])} for r in raw
            ]
            fingerprint = rows_digest(rows)
            if existing:
                if fingerprint != existing["sha256"] or len(rows) != existing["row_count"]:
                    raise RecoveryConflict("Immutable source window integrity mismatch")
            else:
                await c.execute(
                    source_windows.insert().values(
                        session_id=session,
                        version=version,
                        start_sequence=cursor,
                        end_sequence=end,
                        closed_at=closed,
                        row_count=len(rows),
                        sha256=fingerprint,
                    )
                )
            return Window(version, cursor, end, closed, rows, fingerprint)

    async def checkpoint(
        self,
        session: str,
        expected: int,
        state: dict[str, Any],
        events: list[dict[str, object]],
        status: str,
        result: dict[str, object] | None = None,
    ) -> int:
        if status not in ("recoverable", "paused", "complete"):
            raise ValueError("Invalid session status")
        fingerprint = digest({"state": state, "events": events, "status": status, "result": result})
        async with self.database.engine.begin() as c:
            row = (
                (
                    await c.execute(
                        select(paper_sessions)
                        .where(paper_sessions.c.id == session)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            actual = int(row["checkpoint_version"])
            if actual == expected + 1 and row["checkpoint_digest"] == fingerprint:
                return actual  # Commit acknowledgement lost; exact retry is idempotent.
            if actual != expected:
                raise RecoveryConflict("Checkpoint version conflict; stop duplicate worker")
            previous = row["recovery_state"]
            if not previous or state["cursor"] < previous["cursor"]:
                raise RecoveryConflict("Source cursor regressed")
            before = int(previous["simulator"]["event_sequence"])
            after = int(state["simulator"]["event_sequence"])
            if [int(str(e["sequence"])) for e in events] != list(range(before + 1, after + 1)):
                raise RecoveryConflict("Noncontiguous event checkpoint")
            if state["windows"] > previous["windows"]:
                window = (
                    (
                        await c.execute(
                            select(source_windows).where(
                                source_windows.c.session_id == session,
                                source_windows.c.version == expected + 1,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                if (
                    window["end_sequence"] != state["cursor"]
                    or window["start_sequence"] != previous["cursor"]
                ):
                    raise RecoveryConflict("Checkpoint does not match closed window")
                if state["observations"] - previous["observations"] != window["row_count"]:
                    raise RecoveryConflict("Observation count does not match closed window")
            if events:
                await c.execute(
                    paper_events.insert(),
                    [
                        {
                            "session_id": session,
                            "sequence": e["sequence"],
                            "at": datetime.fromisoformat(str(e["at"])),
                            "kind": e["kind"],
                            "payload": e,
                        }
                        for e in events
                    ],
                )
            provenance = {**row["provenance"], "state_digest": digest(state)}
            await c.execute(
                paper_sessions.update()
                .where(paper_sessions.c.id == session)
                .values(
                    checkpoint_version=expected + 1,
                    recovery_state=state,
                    source_cursor=state["cursor"],
                    checkpoint_digest=fingerprint,
                    status=status,
                    checkpoint_at=datetime.now(UTC),
                    provenance=provenance,
                    result=result if result is not None else row["result"],
                )
            )
            return expected + 1

    async def read_window(self, session: str, version: int) -> Window:
        """Read-only verification/export of an already-closed window."""
        async with self.database.engine.connect() as c:
            row = (
                (
                    await c.execute(
                        select(source_windows).where(
                            source_windows.c.session_id == session,
                            source_windows.c.version == version,
                        )
                    )
                )
                .mappings()
                .one()
            )
            raw = (
                (
                    await c.execute(
                        select(books.c.source_sequence, books.c.payload)
                        .where(
                            books.c.source_sequence > row["start_sequence"],
                            books.c.source_sequence <= row["end_sequence"],
                        )
                        .order_by(books.c.source_sequence)
                    )
                )
                .mappings()
                .all()
            )
            rows = [
                {"sequence": int(r["source_sequence"]), "book": dict(r["payload"])} for r in raw
            ]
            if len(rows) != row["row_count"] or rows_digest(rows) != row["sha256"]:
                raise RecoveryConflict("Closed input window failed integrity verification")
            return Window(
                version,
                int(row["start_sequence"]),
                int(row["end_sequence"]),
                row["closed_at"],
                rows,
                row["sha256"],
            )

    async def attempt_start(self, session: str, attempt: str) -> None:
        from crossx.db import paper_attempts

        async with self.database.engine.begin() as c:
            await c.execute(
                paper_attempts.insert().values(
                    id=attempt, session_id=session, started_at=datetime.now(UTC), status="active"
                )
            )

    async def attempt_end(self, attempt: str, status: str, reason: str | None = None) -> None:
        from crossx.db import paper_attempts

        async with self.database.engine.begin() as c:
            await c.execute(
                paper_attempts.update()
                .where(paper_attempts.c.id == attempt)
                .values(ended_at=datetime.now(UTC), status=status, reason=reason)
            )
