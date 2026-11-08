"""Structured operational events and bounded-cardinality research metrics."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

BOOKS = Counter("crossx_books", "Validated persisted book observations", ["exchange", "symbol"])
OPPORTUNITIES = Counter(
    "crossx_opportunities", "Recorded hypothetical opportunities", ["symbol", "buy", "sell"]
)
LAST_BOOK = Gauge(
    "crossx_last_book_timestamp_seconds", "Last persisted book receive time", ["exchange", "symbol"]
)
SYNCHRONIZED = Gauge(
    "crossx_book_synchronized", "Current adapter synchronization state", ["exchange", "symbol"]
)
RECONNECTS = Gauge("crossx_reconnects", "Adapter reconnect attempts", ["exchange"])
INTEGRITY_ERRORS = Gauge("crossx_integrity_errors", "Adapter integrity failures", ["exchange"])
QUEUE_DEPTH = Gauge("crossx_queue_depth", "Observations waiting for persistence")
PERSIST_SECONDS = Histogram("crossx_persist_seconds", "Book persistence latency")
NET_PROFIT = Histogram(
    "crossx_estimated_net_profit_usd", "Estimated profit per observation; not realized P&L",
    buckets=(0.001, 0.01, 0.1, 1, 5, 10, 50, 100),
)
SHUTDOWN_TIMEOUTS = Counter(
    "crossx_shutdown_timeouts", "Shutdown grace expired before accepted writes completed"
)
READY = Gauge("crossx_ready", "Database initialized and application consuming")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ("exchange", "symbol", "reason", "net_profit", "buy", "sell"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
