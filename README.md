# CrossX — Phase 1

Public market-data research for **BTC/USD and ETH/USD** on **Kraken and Coinbase**.
CrossX reconstructs validated spot order books, persists timestamped observations,
and records hypothetical fee-adjusted cross-exchange opportunities. It contains
**no order submission, live execution, exchange credentials, margin or derivatives**.
No paid infrastructure is needed.

## Start locally

Requirements: Docker with Compose, outbound HTTPS/WebSockets, and free local ports
5432, 8000, 9090 and 3000.

```sh
cp .env.example .env
docker compose up -d --build
docker compose logs -f app
```

The application runs Alembic before ingestion. PostgreSQL uses a named volume;
all published ports bind to localhost. Local database credentials are development
credentials only. The default fee schedule is unknown: **books are collected but
opportunities are disabled** until both relevant taker fees are explicitly supplied.

Edit `CROSSX_FEES` in `.env` using independently verified fee assumptions, represented
as fractional decimal strings. For example, `"0.004"` means 0.4%. The commented
example is illustrative and does not represent a current verified fee tier. Restart
with `docker compose up -d --force-recreate app` after changing configuration.

- [Metrics](http://localhost:8000/metrics)
- [Prometheus](http://localhost:9090)
- [Grafana: CrossX Phase 1](http://localhost:3000/d/crossx-phase1)

Inspect collected data:

```sh
docker compose exec -T postgres psql -U crossx -d crossx -c \
  'SELECT exchange, symbol, count(*), max(received_at) FROM book_observations GROUP BY exchange, symbol;'
docker compose exec -T postgres psql -U crossx -d crossx -c \
  'SELECT observed_at, payload FROM opportunities ORDER BY id DESC LIMIT 5;'
docker compose stop
```

`docker compose down` removes containers while preserving the database volume.
Collection stores every emitted observation; monitor disk space and stop collection
when sufficient research data exists. No retention deletion runs automatically.

## Development and validation

Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker and Compose:

```sh
uv sync --frozen --extra dev
uv run --frozen ruff check .
uv run --frozen mypy src
uv run --frozen pytest -q
# The database integration test is skipped unless its explicit test URL is supplied.
docker compose up -d postgres
uv run --frozen alembic upgrade head
CROSSX_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:5432/crossx \
  uv run --frozen pytest -q
uv run --frozen python scripts/check_connectivity.py --timeout 30
docker compose config --quiet
docker compose build app
```

The connectivity probe uses only public subscriptions, requires both symbols from
both venues and exits nonzero on failure. Unit tests are deterministic and use no
network. The PostgreSQL test inserts its own records and removes only their IDs.
Use a dedicated development database for tests. CI runs migration, tests, Ruff,
strict mypy, Compose validation and a container build; live exchange connectivity
is deliberately a separate acceptance check.

`uv.lock` locks development and runtime dependencies. `requirements.lock` is the
hashed runtime export consumed by Docker. After intentional dependency updates,
regenerate both together:

```sh
uv lock
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

## Research assumptions

- Fee-adjusted net USD profit = sell VWAP × quantity − buy VWAP × quantity −
  taker fees on both legs. Maker fees are configurable but are not used to claim
  immediate fills. Unknown fees never become zero; explicit zero is allowed.
- Displayed depth is consumed level by level, with no extrapolation. Both adapters
  publish the top 25 levels; Coinbase maintains the full received book internally.
- Books must be synchronized, fresh by both event and receive clocks, and within
  the configured exchange-time skew. Heartbeats do not refresh market observations.
- Signals assume cash on the buy venue and spot inventory on the sell venue.
  No balances are reserved and no fills are simulated. Repeated signals can refer
  to the same displayed liquidity and must not be summed as a trading P&L.
- Transfer/rebalancing costs, GBP conversion, latency, adverse selection, minimum
  order sizes and trading increments are not modeled. USD observations alone do
  not establish feasibility for a £500–£1,500 allocation. No profitability is assumed.

Persistence calls and database initialization have a configurable 10-second deadline
(`CROSSX_PERSISTENCE_TIMEOUT_SECONDS`). On SIGTERM/SIGINT, producers close their
feeds and accepted observations drain for up to five seconds
(`CROSSX_SHUTDOWN_TIMEOUT_SECONDS`). Expiry logs `shutdown_drain_timeout` and
increments `crossx_shutdown_timeouts_total`; unfinished work can still be lost.
If increasing shutdown grace, also increase Docker's stop timeout beyond that
value. [Independent review](docs/PHASE1_INDEPENDENT_REVIEW.md) includes bounded
fault-test commands, findings, residual security risks and review evidence.

Configuration is environment-only (`CROSSX_` prefix); see `.env.example`,
`src/crossx/config.py` and [architecture](docs/ARCHITECTURE.md).
[Phase 1 acceptance report](docs/PHASE1_ACCEPTANCE.md) records executed checks,
known limits and human acceptance steps.

## Phase 1.2 operational hardening

See [the measured hardening report](docs/PHASE1_2_HARDENING.md). Grafana uses anonymous Viewer access with basic authentication and initial admin creation disabled. Bounded ingestion uses immediate transactions of up to 32 observations; the queue remains 128. Storage telemetry and local Prometheus alerts cover feed freshness, persistence, queue pressure and disk budgets. No external alert delivery is configured.

Full observation history remains the default. `CROSSX_BOOK_SAMPLE_INTERVAL_SECONDS` optionally limits routine persisted history per exchange/symbol while evaluating every validated book and retaining supporting observations for detected signals. Sampling reduces historical resolution. `CROSSX_RETENTION_DAYS` only enables a read-only eligibility report: `python -m crossx.storage --plan`. No observations or volumes expire automatically. Archive and verify research evidence before any separately authorised manual retention operation.
