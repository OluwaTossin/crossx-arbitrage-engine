import hashlib
import json
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from test_paper import book, seeded, trigger

from crossx.archive import verify
from crossx.paper_metrics import PaperMetrics


def test_archive_verifies_and_rejects_corruption(tmp_path: Path):
    data = (book().model_dump_json() + "\n").encode()
    (tmp_path / "books.jsonl").write_bytes(data)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"sha256": hashlib.sha256(data).hexdigest(), "observations": 1})
    )
    assert verify(tmp_path) == 1
    (tmp_path / "books.jsonl").write_bytes(data + data)
    with pytest.raises(ValueError, match="mismatch"):
        verify(tmp_path)


def test_archive_detects_receipt_reordering(tmp_path):
    data = (book(ms=1).model_dump_json() + "\n" + book(ms=0).model_dump_json() + "\n").encode()
    (tmp_path / "books.jsonl").write_bytes(data)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"sha256": hashlib.sha256(data).hexdigest(), "observations": 2})
    )
    with pytest.raises(ValueError, match="ordering"):
        verify(tmp_path)


def test_paper_metrics_bounded_and_latest_result():
    sim, _ = seeded(fail_second_leg=True)
    trigger(sim)
    registry = CollectorRegistry()
    metrics = PaperMetrics(registry)
    metrics.update(None)
    metrics.update(sim.finish())
    text = generate_latest(registry).decode()
    assert "crossx_paper_result_available 1.0" in text
    assert 'crossx_paper_unhedged_quantity{asset="BTC"} 0.1' in text
    assert "session_id" not in text
    assert 'crossx_paper_events{stage="failed_leg"} 1.0' in text


def test_quote_assets_never_silently_compared():
    from decimal import Decimal

    from test_paper import T

    from crossx.intelligence import detect_opportunities
    from crossx.schemas import FeeSchedule

    a = book().model_copy(update={"symbol": "BTC/USDT"})
    b = book("coinbase", bid="110", ask="111").model_copy(update={"symbol": "BTC/USDT"})
    assert (
        detect_opportunities(
            [a, b],
            {"kraken": FeeSchedule(taker=0), "coinbase": FeeSchedule(taker=0)},
            Decimal(".1"),
            T,
        )
        == []
    )


@pytest.mark.integration
async def test_paper_checkpoint_atomic_and_streaming_replay():
    import os
    from uuid import uuid4

    from sqlalchemy import delete, select
    from sqlalchemy.exc import IntegrityError
    from test_paper import T

    from crossx.db import Database, paper_events, paper_sessions

    url = os.getenv("CROSSX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated migrated PostgreSQL")
    db = Database(url)
    session = str(uuid4())
    sim, events = seeded()
    trigger(sim)
    result = sim.finish()
    try:
        await db.start_session(session, sim.config.model_dump(mode="json"), {"test": True})
        # Duplicate keys roll back the entire batch and final result update.
        with pytest.raises(IntegrityError):
            await db.paper_checkpoint(session, [events[0], events[0]], result)
        async with db.engine.connect() as c:
            assert not (
                await c.execute(select(paper_events).where(paper_events.c.session_id == session))
            ).all()
            assert (
                await c.scalar(
                    select(paper_sessions.c.result).where(paper_sessions.c.id == session)
                )
                is None
            )
        await db.paper_checkpoint(session, events, result)
        assert (await db.latest_paper_result())["counts"]["trade_completed"] == 1
        from datetime import timedelta

        rows = [b async for b in db.iter_books(T, T + timedelta(seconds=1))]
        assert rows == sorted(rows, key=lambda b: b.received_at)
    finally:
        async with db.engine.begin() as c:
            await c.execute(delete(paper_events).where(paper_events.c.session_id == session))
            await c.execute(delete(paper_sessions).where(paper_sessions.c.id == session))
        await db.close()


@pytest.mark.integration
async def test_forward_watermark_detects_late_new_insert():
    import os
    from datetime import timedelta

    from sqlalchemy import delete
    from test_paper import T

    from crossx.db import Database, books
    from crossx.forward import late_watermark

    url = os.getenv("CROSSX_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL")
    db = Database(url)
    stamp = T + timedelta(days=33)
    try:
        last, _ = await late_watermark(db, 0, stamp)
        await db.save_book(book().model_copy(update={"received_at": stamp - timedelta(seconds=1)}))
        _, late = await late_watermark(db, last, stamp)
        assert late == 1
    finally:
        async with db.engine.begin() as c:
            await c.execute(
                delete(books).where(books.c.received_at == stamp - timedelta(seconds=1))
            )
        await db.close()


def test_adapters_have_no_authenticated_exchange_operations():
    import ast

    from crossx.adapters import REGISTRY

    root = Path(__file__).parents[1] / "src/crossx/adapters"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not node.value.startswith(("private/", "/api/v3/order", "/sapi/"))
    for cls in REGISTRY.values():
        adapter = cls(("BTC/USD", "ETH/USD"))
        for subscription in adapter.subscriptions():
            assert subscription.get("method", subscription.get("type")) in (
                "subscribe",
                "SUBSCRIBE",
            )
            assert not any(k in subscription for k in ("signature", "api_key", "jwt", "token"))
