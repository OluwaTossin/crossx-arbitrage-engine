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
    "crossx_estimated_net_profit_usd",
    "Estimated profit per observation; not realized P&L",
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


INGRESS = Counter(
    "crossx_ingress_books", "Validated books yielded by adapters", ["exchange", "symbol"]
)
LAST_RECEIVED = Gauge(
    "crossx_last_received_timestamp_seconds", "Latest ingress receive clock", ["exchange", "symbol"]
)
LAST_EVENT = Gauge(
    "crossx_last_event_timestamp_seconds",
    "Unmodified latest exchange observation clock",
    ["exchange", "symbol"],
)
EVENT_RECEIVE_AGE = Histogram(
    "crossx_event_age_at_receive_seconds",
    "Nonnegative observation-to-receive age; not network RTT",
    ["exchange", "symbol"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 30, 60),
)
PRODUCER_DELAY = Histogram(
    "crossx_producer_delay_seconds",
    "Receive to adapter yield (decode/reconstruction/scheduling)",
    ["exchange"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10),
)
BACKPRESSURE = Histogram(
    "crossx_queue_admission_seconds",
    "Time awaiting a bounded queue slot",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10),
)
QUEUE_RESIDENCE = Histogram(
    "crossx_queue_residence_seconds",
    "Successful queue admission to dequeue",
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10),
)
IN_FLIGHT = Gauge("crossx_in_flight_books", "Dequeued books currently being processed")
BATCH_SIZE = Histogram(
    "crossx_persist_batch_size",
    "Books per database transaction request",
    buckets=(1, 2, 4, 8, 16, 32, 64, 128),
)
TRANSACTION_SECONDS = Histogram(
    "crossx_persist_transaction_seconds", "Whole book transaction request latency"
)
PERSIST_FAILURES = Counter(
    "crossx_persistence_failures", "Failed persistence requests", ["operation"]
)
REJECTIONS = Counter(
    "crossx_detection_rejections",
    "Incoming observations excluded from detection",
    ["exchange", "symbol", "reason"],
)
EVENT_EVALUATION_AGE = Histogram(
    "crossx_event_age_at_evaluation_seconds",
    "Nonnegative event age after persistence",
    ["exchange", "symbol"],
    buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10, 30, 60),
)
SAMPLED_OUT = Counter(
    "crossx_storage_sampled_out_books",
    "Books deliberately omitted from sampled history",
    ["exchange", "symbol"],
)
RECONSTRUCTION = Histogram(
    "crossx_reconstruction_seconds",
    "JSON decoding and book validation time",
    ["exchange"],
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5),
)
