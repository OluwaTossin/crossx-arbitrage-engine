"""Exporter cancellation must release both resources without altering telemetry semantics."""

import asyncio
import signal
from unittest.mock import Mock

import pytest
from prometheus_client import generate_latest

from crossx import storage
from crossx.config import Settings


@pytest.fixture
def resources(monkeypatch):
    db = Mock()
    polled = asyncio.Event()
    closing = asyncio.Event()
    closed = asyncio.Event()

    async def usage():
        polled.set()
        return 1234, 567

    async def result():
        return None

    async def close():
        closing.set()
        closed.set()

    db.storage_usage = usage
    db.latest_paper_result = result
    db.close = close
    server, thread = Mock(), Mock()
    thread.is_alive.return_value = False
    captured = {}

    def start(port, *, registry):
        captured['registry'] = registry
        return server, thread

    monkeypatch.setattr(storage, 'Database', lambda url: db)
    monkeypatch.setattr(storage, 'start_http_server', start)
    return db, polled, closing, closed, server, thread, captured


@pytest.mark.parametrize('during_poll', [False, True])
async def test_cancel_releases_pool_http_socket_and_thread(resources, tmp_path, during_poll):
    db, polled, _, closed, server, thread, captured = resources
    cancelled = asyncio.Event()
    if during_poll:
        async def pending_usage():
            polled.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        db.storage_usage = pending_usage
    task = asyncio.create_task(storage.export(Settings(), str(tmp_path), 0))
    await asyncio.wait_for(polled.wait(), 1)
    if not during_poll:
        text = generate_latest(captured['registry']).decode()
        assert 'crossx_storage_up 1.0' in text
        assert 'crossx_storage_database_bytes 1234.0' in text
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert closed.is_set()
    assert not during_poll or cancelled.is_set()
    server.shutdown.assert_called_once()
    server.server_close.assert_called_once()
    thread.join.assert_called_once_with(timeout=0.5)


async def test_database_failure_keeps_scrape_available_and_no_false_success(resources, tmp_path):
    db, polled, _, closed, server, _, captured = resources
    async def unavailable():
        polled.set()
        raise OSError('unavailable')
    db.storage_usage = unavailable
    task = asyncio.create_task(storage.export(Settings(), str(tmp_path), 0))
    await asyncio.wait_for(polled.wait(), 1)
    text = generate_latest(captured['registry']).decode()
    assert 'crossx_storage_up 0.0' in text
    assert 'crossx_storage_last_success_timestamp_seconds 0.0' in text
    assert not closed.is_set()
    server.shutdown.assert_not_called()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


async def test_pool_disposal_timeout_is_bounded_after_http_cleanup(resources, tmp_path):
    db, polled, _, _, server, _, _ = resources
    disposed = asyncio.Event()
    async def stuck_close():
        try:
            await asyncio.Event().wait()
        finally:
            disposed.set()
    db.close = stuck_close
    task = asyncio.create_task(storage.export(
        Settings(shutdown_timeout_seconds=0.02), str(tmp_path), 0
    ))
    await asyncio.wait_for(polled.wait(), 1)
    task.cancel()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, 1)
    assert disposed.is_set()
    server.server_close.assert_called_once()


async def test_listener_start_failure_still_disposes_pool(resources, monkeypatch, tmp_path):
    _, _, _, closed, _, _, _ = resources
    def fail(*args, **kwargs):
        raise OSError('address in use')
    monkeypatch.setattr(storage, 'start_http_server', fail)
    with pytest.raises(OSError, match='address in use'):
        await storage.export(Settings(), str(tmp_path), 0)
    assert closed.is_set()


async def test_repeated_termination_does_not_cancel_cleanup(monkeypatch):
    handlers = {}
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, 'add_signal_handler', lambda sig, fn: handlers.update({sig: fn}))
    removed = []
    monkeypatch.setattr(loop, 'remove_signal_handler', lambda sig: removed.append(sig))
    running, cleaning, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def exporter(*args):
        running.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish.wait()
    monkeypatch.setattr(storage, 'export', exporter)
    task = asyncio.create_task(storage.serve(Settings(), '/', 0))
    await running.wait()
    handlers[signal.SIGTERM]()
    await cleaning.wait()
    handlers[signal.SIGTERM]()
    handlers[signal.SIGINT]()
    finish.set()
    await asyncio.wait_for(task, 1)
    assert removed == [signal.SIGTERM, signal.SIGINT]
