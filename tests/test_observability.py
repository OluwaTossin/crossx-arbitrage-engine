import json
import logging
import sys
from datetime import datetime
from decimal import Decimal

from prometheus_client import REGISTRY, generate_latest

from crossx import observability


def test_json_logs_preserve_context_and_exception() -> None:
    record = logging.LogRecord(
        "crossx.test", logging.WARNING, __file__, 1, "Rejected %s", ("book",), None
    )
    record.exchange = "kraken"
    record.symbol = "BTC/USD"
    record.reason = "stale"
    record.net_profit = Decimal("0.123456789123456789")
    try:
        raise ValueError("invalid checksum")
    except ValueError:
        record.exc_info = sys.exc_info()
    payload = json.loads(observability.JsonFormatter().format(record))
    assert payload["event"] == "Rejected book"
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "crossx.test"
    assert payload["exchange"] == "kraken"
    assert payload["symbol"] == "BTC/USD"
    assert payload["reason"] == "stale"
    assert payload["net_profit"] == "0.123456789123456789"
    assert "ValueError: invalid checksum" in payload["exception"]
    assert datetime.fromisoformat(payload["timestamp"]).utcoffset().total_seconds() == 0


def test_metrics_export_expected_labels_and_latency_buckets() -> None:
    labels = {"exchange": "kraken", "symbol": "BTC/USD"}
    before = REGISTRY.get_sample_value("crossx_books_total", labels) or 0
    observability.BOOKS.labels(**labels).inc()
    observability.LAST_BOOK.labels(**labels).set(1234567890)
    observability.SYNCHRONIZED.labels(**labels).set(1)
    observability.PERSIST_SECONDS.observe(0.015)
    observability.NET_PROFIT.observe(0.125)
    observability.READY.set(1)
    assert REGISTRY.get_sample_value("crossx_books_total", labels) == before + 1
    exposition = generate_latest().decode()
    assert 'crossx_last_book_timestamp_seconds{exchange="kraken",symbol="BTC/USD"}' in exposition
    assert 'crossx_book_synchronized{exchange="kraken",symbol="BTC/USD"} 1.0' in exposition
    assert "crossx_persist_seconds_bucket" in exposition
    assert "crossx_estimated_net_profit_usd_bucket" in exposition
    assert "crossx_ready 1.0" in exposition
