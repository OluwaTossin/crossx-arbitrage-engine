import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from test_paper import T, book, seeded, trigger

from crossx.db import Database, books, paper_events
from crossx.forward import process_window
from crossx.forward_store import ForwardStore, RecoveryConflict, Window
from crossx.recovery import canonical, restore, snapshot


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("boundary", [0, 1, 2, 3])
def test_crash_reservation_execution_settlement_matches_uninterrupted(boundary, partial):
    def prefix():
        sim, events = seeded(second_leg_fill_fraction=".5" if partial else "1")
        return sim, events

    steps = [
        lambda s: trigger(s),
        lambda s: s.advance(T + timedelta(milliseconds=11)),
        lambda s: s.advance(T + timedelta(milliseconds=21)),
    ]
    reference, expected = prefix()
    for step in steps:
        step(reference)
    target = reference.finish()
    sim, events = prefix()
    for step in steps[:boundary]:
        step(sim)
    saved = snapshot(sim)
    committed_events = list(events)
    # Execute uncommitted state changes and lose the process before a checkpoint.
    for step in steps[boundary:]:
        step(sim)
    recovered = restore(json.loads(canonical(saved)), committed_events.append)
    for step in steps[boundary:]:
        step(recovered)
    assert recovered.finish() == target
    assert committed_events == expected
    assert all(w.reserved_cash == 0 for w in recovered.wallets.values())


def test_recovery_preserves_pending_partial_buy_cost_basis_and_risk():
    sim, events = seeded()
    trigger(sim)
    sim.ingest(book("kraken", quantity=".03", ms=5))
    sim.advance(T + timedelta(milliseconds=11))
    saved = snapshot(sim)
    rebuilt = restore(saved, lambda e: None)
    assert rebuilt.pending[0][3].buy_quantity == sim.pending[0][3].buy_quantity
    assert snapshot(rebuilt) == saved
    assert rebuilt.finish() == sim.finish()
    broken = json.loads(canonical(saved))
    broken["wallets"]["kraken"]["reserved_cash"] = "-1"
    with pytest.raises(ValueError, match="cash"):
        restore(broken, lambda e: None)


def test_closed_window_does_not_rewrite_receipt_or_look_ahead():
    sim, events = seeded()
    trigger(sim)
    future = book("coinbase", bid="500", ask="501", ms=30)
    window = Window(
        1,
        0,
        1,
        T + timedelta(milliseconds=30),
        [{"sequence": 1, "book": future.model_dump(mode="json")}],
        "unused",
    )
    process_window(sim, window)
    assert [e for e in events if e["kind"] == "leg"][1]["notional"] == "11.0"
    assert sim.latest[("coinbase", "BTC/USD")].received_at == future.received_at


@pytest.fixture
async def db():
    url = os.getenv("CROSSX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated protocol-migrated PostgreSQL")
    database = Database(url)
    yield database
    await database.close()


@pytest.mark.integration
async def test_delayed_commit_cannot_appear_behind_source_checkpoint(db):
    # Numeric row ids can be allocated before commit; our trigger locks BEFORE
    # source-sequence allocation and holds the lock until transaction outcome.
    first = await db.engine.connect()
    t1 = await first.begin()
    second = await db.engine.connect()
    t2 = await second.begin()
    store = ForwardStore(db)
    session = str(uuid4())
    await store.begin(session, snapshot(seeded()[0]), {"test": True}, False)
    row = dict(
        exchange="kraken",
        symbol="BTC/USD",
        observed_at=T,
        received_at=T,
        payload=book().model_dump(mode="json"),
    )
    try:
        seq1 = await first.scalar(books.insert().values(**row).returning(books.c.source_sequence))
        task = asyncio.create_task(
            second.scalar(books.insert().values(**row).returning(books.c.source_sequence))
        )
        await asyncio.sleep(0.03)
        assert not task.done()
        window_task = asyncio.create_task(store.window(session, 1, 0))
        await asyncio.sleep(0.03)
        assert not window_task.done()  # Closure also waits for unfinished writer.
        await t1.commit()
        seq2 = await asyncio.wait_for(task, 2)
        assert seq2 > seq1
        await t2.commit()
        window = await asyncio.wait_for(window_task, 2)
        assert [r["sequence"] for r in window.rows][-2:] == [seq1, seq2]
        replay = await store.window(session, 1, 0)
        assert replay == window
        async with db.engine.begin() as c:
            with pytest.raises(DBAPIError, match="immutable"):
                await c.execute(
                    update(books).where(books.c.source_sequence == seq1).values(payload={})
                )
    finally:
        if t1.is_active:
            await t1.rollback()
        if t2.is_active:
            await t2.rollback()
        await first.close()
        await second.close()


@pytest.mark.integration
async def test_window_timeout_rollbacks_and_retry_is_deterministic(db):
    store = ForwardStore(db)
    session = str(uuid4())
    initial = await store.begin(session, snapshot(seeded()[0]), {"test": True}, False)
    c = await db.engine.connect()
    tx = await c.begin()
    try:
        await c.execute(text("SELECT pg_advisory_xact_lock(728406310427)"))
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.03):
                await store.window(session, 1, initial["state"]["cursor"])
    finally:
        await tx.rollback()
        await c.close()
    window = await store.window(session, 1, initial["state"]["cursor"])
    assert await store.window(session, 1, initial["state"]["cursor"]) == window


@pytest.mark.integration
async def test_atomic_state_cursor_events_checkpoint_idempotent_and_fenced(db):
    store = ForwardStore(db)
    session = str(uuid4())
    events = []
    sim = seeded()[0]
    initial = await store.begin(session, snapshot(sim), {"test": True}, False)
    # State must include the ledger's allocation sequence already seeded.
    sim.emit = events.append
    window = await store.window(session, 1, initial["state"]["cursor"])
    process_window(sim, window)
    state = {
        "simulator": snapshot(sim),
        "cursor": window.end,
        "windows": 1,
        "observations": len(window.rows),
    }
    v = await store.checkpoint(session, 0, state, events, "recoverable")
    assert v == 1
    assert await store.checkpoint(session, 0, state, events, "recoverable") == 1
    loaded = await store.load(session)
    assert loaded["state"] == state
    with pytest.raises(RecoveryConflict, match="version"):
        await store.checkpoint(session, 0, state, [], "paused")
    assert (await store.load(session))["version"] == 1


@pytest.mark.integration
async def test_checkpoint_bad_event_identity_rolls_back_all_state(db):
    store = ForwardStore(db)
    session = str(uuid4())
    sim = seeded()[0]
    initial = await store.begin(session, snapshot(sim), {"test": True}, False)
    events = []
    sim.emit = events.append
    trigger(sim)
    state = {**initial["state"], "simulator": snapshot(sim)}
    with pytest.raises(RecoveryConflict, match="Noncontiguous"):
        await store.checkpoint(session, 0, state, [], "paused")
    assert (await store.load(session))["state"] == initial["state"]
    async with db.engine.connect() as c:
        assert not (
            await c.execute(select(paper_events).where(paper_events.c.session_id == session))
        ).all()
    assert await store.checkpoint(session, 0, state, events, "paused") == 1
    assert restore((await store.load(session))["state"]["simulator"], lambda e: None).pending


@pytest.mark.integration
async def test_legacy_session_is_irrecoverable_not_silently_reinitialized(db):
    session = str(uuid4())
    await db.start_session(session, {}, {"test": True})
    with pytest.raises(RecoveryConflict, match="no recoverable"):
        await ForwardStore(db).load(session)


@pytest.mark.integration
@pytest.mark.parametrize("phase", ["reservation", "buy", "settlement"])
async def test_real_process_exit_loses_only_uncommitted_work(db, tmp_path, phase):
    import subprocess
    import sys

    sim, committed = seeded(second_leg_fill_fraction=".5")
    session = str(uuid4())
    store = ForwardStore(db)
    await store.begin(session, snapshot(sim), {"test": "process_exit"}, False)
    saved = (await store.load(session))["state"]["simulator"]
    file = tmp_path / "snapshot.json"
    file.write_bytes(canonical(saved))
    code = """import json,os,sys
from datetime import UTC, datetime, timedelta
from crossx.recovery import restore
from test_paper import T,trigger
s=restore(json.load(open(sys.argv[1])),lambda e:None)
trigger(s)
if sys.argv[2] in ('buy','settlement'):s.advance(T+timedelta(milliseconds=11))
if sys.argv[2]=='settlement':s.advance(T+timedelta(milliseconds=21))
os._exit(23)
"""
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code, str(file), phase],
        env={**os.environ, "PYTHONPATH": str(__import__("pathlib").Path("tests").resolve())},
        check=False,
    )
    assert result.returncode == 23
    recovered = restore((await store.load(session))["state"]["simulator"], committed.append)
    trigger(recovered)
    expected, original = seeded(second_leg_fill_fraction=".5")
    trigger(expected)
    assert recovered.finish() == expected.finish()
    assert committed == original


@pytest.mark.integration
async def test_database_error_after_event_insert_rolls_back_settlement(db):
    store = ForwardStore(db)
    session = str(uuid4())
    sim = seeded()[0]
    initial = await store.begin(session, snapshot(sim), {"test": True}, False)
    events = []
    sim.emit = events.append
    trigger(sim)
    sim.finish()
    state = {**initial["state"], "simulator": snapshot(sim)}
    corrupted = [dict(e) for e in events]
    corrupted[-1]["kind"] = "x" * 100
    with pytest.raises(DBAPIError):
        await store.checkpoint(session, 0, state, corrupted, "paused")
    assert (await store.load(session))["state"] == initial["state"]
    async with db.engine.connect() as c:
        assert not (
            await c.execute(select(paper_events).where(paper_events.c.session_id == session))
        ).all()
    assert await store.checkpoint(session, 0, state, events, "paused") == 1


@pytest.mark.integration
async def test_closed_window_export_preserves_input_and_detects_damage(db, tmp_path, monkeypatch):
    from crossx.window_archive import export, verify

    session = str(uuid4())
    store = ForwardStore(db)
    initial = await store.begin(session, snapshot(seeded()[0]), {"test": True}, False)
    await db.save_book(book().model_copy(update={"received_at": datetime.now(UTC)}))
    window = await store.window(session, 1, initial["state"]["cursor"])
    monkeypatch.setenv(
        "CROSSX_DATABASE_URL", str(db.engine.url.render_as_string(hide_password=False))
    )
    await export(session, 1, tmp_path / "archive", False)
    assert verify(tmp_path / "archive") == window
    file = tmp_path / "archive" / "source.jsonl"
    file.write_text(file.read_text() + file.read_text())
    with pytest.raises(ValueError, match="checksum"):
        verify(tmp_path / "archive")


def test_source_closure_clock_regression_fails_closed():
    sim, _ = seeded()
    with pytest.raises(RecoveryConflict, match="clock"):
        process_window(sim, Window(1, 0, 0, T - timedelta(seconds=1), [], "unused"))


def test_aggregate_diagnostics_preserve_financial_results_and_all_counters():
    outcomes = []
    for mode in ("all", "aggregate"):
        sim, events = seeded(evaluation_log_mode=mode)
        trigger(sim)
        result = sim.finish()
        result.pop("assumptions")
        outcomes.append((result, events))
    assert outcomes[0][0] == outcomes[1][0]
    assert len(outcomes[1][1]) < len(outcomes[0][1])
    financial = {
        "allocation",
        "capital_feasible",
        "leg",
        "trade_completed",
        "trade_unhedged",
        "failed_leg",
        "partial_fill",
    }
    for kind in financial:
        assert sum(e["kind"] == kind for e in outcomes[0][1]) == sum(
            e["kind"] == kind for e in outcomes[1][1]
        )
    assert [e["sequence"] for e in outcomes[1][1]] == list(range(1, len(outcomes[1][1]) + 1))


def test_pre_aggregation_snapshot_keeps_all_event_policy_on_restore():
    sim, _ = seeded()
    trigger(sim)
    old = snapshot(sim)
    old["config"].pop("evaluation_log_mode")
    recovered = restore(old, lambda e: None)
    assert recovered.config.evaluation_log_mode == "all"
    assert recovered.finish() == sim.finish()


@pytest.mark.integration
async def test_numeric_ids_can_commit_out_of_order_without_source_loss(db):
    store = ForwardStore(db)
    session = str(uuid4())
    initial = await store.begin(session, snapshot(seeded()[0]), {"test": "reverse_ids"}, False)
    async with db.engine.begin() as c:
        older_id = await c.scalar(
            text("SELECT nextval(pg_get_serial_sequence('book_observations','id'))")
        )
    await db.save_book(book())
    async with db.engine.begin() as c:
        await c.execute(
            books.insert().values(
                id=older_id,
                exchange="kraken",
                symbol="BTC/USD",
                observed_at=T,
                received_at=T,
                payload=book(quantity="2").model_dump(mode="json"),
            )
        )
        ids = (
            (
                await c.execute(
                    select(books.c.id)
                    .where(books.c.source_sequence > initial["state"]["cursor"])
                    .order_by(books.c.source_sequence)
                )
            )
            .scalars()
            .all()
        )
    assert ids[0] > ids[1]
    window = await store.window(session, 1, initial["state"]["cursor"])
    assert len(window.rows) == 2
    assert window.rows[0]["sequence"] < window.rows[1]["sequence"]


@pytest.mark.integration
async def test_rolled_back_sequence_gap_is_not_a_missing_observation(db):
    store = ForwardStore(db)
    session = str(uuid4())
    initial = await store.begin(session, snapshot(seeded()[0]), {"test": "rollback_gap"}, False)
    c = await db.engine.connect()
    transaction = await c.begin()
    await c.execute(
        books.insert().values(
            exchange="kraken",
            symbol="BTC/USD",
            observed_at=T,
            received_at=T,
            payload=book().model_dump(mode="json"),
        )
    )
    await transaction.rollback()
    await c.close()
    await db.save_book(book())
    window = await store.window(session, 1, initial["state"]["cursor"])
    assert len(window.rows) == 1
    assert window.end > initial["state"]["cursor"] + 1


def test_timer_settlement_is_independent_of_callers_decimal_precision():
    from decimal import localcontext

    results = []
    for precision in (6, 50):
        sim, _ = seeded(second_leg_fill_fraction=".5")
        trigger(sim)
        with localcontext() as context:
            context.prec = precision
            sim.advance(T + timedelta(milliseconds=30))
        results.append(sim.finish())
    assert results[0] == results[1]
