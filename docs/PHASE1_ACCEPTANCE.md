# Phase 1 acceptance report

Date: 7 November 2026 (Europe/London). Scope: public BTC/USD and ETH/USD market
research on Kraken and Coinbase. No exchange account credentials, order submission,
live fills, leverage, margin, derivatives or paid cloud infrastructure were added.

## Implemented deliverables

| Deliverable | Implementation |
| --- | --- |
| Foundation | Python 3.12 package, immutable Pydantic v2 schemas, Decimal, environment configuration, locked dependencies |
| Adapters | Public Kraken v2 CRC32 depth-25 books; Coinbase Advanced Trade level2 snapshot/update reconstruction; heartbeats, sequence integrity, timeout, backoff and reconnect invalidation |
| Persistence | PostgreSQL, Alembic revision 0001, timestamped book and opportunity JSONB with exact Decimal strings |
| Intelligence | Whole-quantity VWAP in both directions, taker fees on both legs, unknown-fee rejection, liquidity, freshness, skew and synchronization checks |
| Tests | Deterministic replay, official checksum vector, reconnect and generation regressions, real PostgreSQL roundtrip and parser-to-database opportunity pipeline |
| Deployment | Locally built application, Prometheus and Grafana images plus PostgreSQL via Docker Compose |
| CI | Locked install, Ruff, strict mypy, PostgreSQL migration and tests, Compose validation, all image builds |
| Observability | JSON logs, feed health, book age, observations, integrity errors, reconnects, queue, latency and signal metrics; provisioned dashboard |
| Documentation | README, architecture, shared interfaces and this report |

## Executed validation

Environment: macOS arm64, Python 3.12.15, Docker server 29.8.2. Tests run against
local PostgreSQL 16. Networked checks required the execution environment's elevated
network/Docker access. These were local checks; GitHub Actions results are separate.

| Command | Observed result |
| --- | --- |
| `UV_CACHE_DIR=/private/tmp/crossx-uv-cache uv sync --frozen --extra dev` | Succeeded, locked environment installed |
| `.venv/bin/alembic upgrade head` | Succeeded against local PostgreSQL |
| `CROSSX_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:5432/crossx .venv/bin/pytest -q` | **83 passed**, including two real PostgreSQL integration tests |
| `.venv/bin/ruff check .` | Passed |
| `.venv/bin/mypy src` | Passed, 11 source files |
| `UV_CACHE_DIR=/private/tmp/crossx-uv-cache uv lock --check --offline` | Passed |
| `git diff --check` | Passed |
| `docker compose config --quiet` | Passed |
| `docker compose build app` | Succeeded with hash-locked runtime dependencies |
| `docker compose up -d --build` | All four services started; monitoring changed to packaged config after bind-mount startup issue |
| `docker compose up -d --build prometheus grafana` | Both monitoring images built; initial stuck mounted container removed and packaged replacement started |
| `PYTHONPATH=src .venv/bin/python scripts/check_connectivity.py --timeout 25` | Exit 0; both symbols on both exchanges received and validated |
| `PYTHONPATH=src .venv/bin/python scripts/check_connectivity.py --timeout 55 --duration 35` | Exit 0; Coinbase BTC 554 / ETH 408 and Kraken BTC 529 / ETH 851 observations; zero reconnects or integrity failures |
| HTTP `GET http://127.0.0.1:8000/metrics` | 200; book counters, ready state and feed health exposed |
| HTTP `GET http://127.0.0.1:9090/-/ready` and `/api/v1/targets` | 200; CrossX target health `up`, lastError empty |
| HTTP `GET http://127.0.0.1:3000/api/health` and `/api/search?query=CrossX` | 200; database `ok`, provisioned `crossx-phase1` dashboard found |
| PostgreSQL aggregation of observations | All four exchange/symbol combinations present |

Final running-container verification at 22:43 UTC showed synchronized state 1 for
all four pairs, queue depth 0, and zero reconnects/integrity failures since the
corrected container started at 22:42:21 UTC. The database contained current live
observations for all four pairs. This short run is not a long-duration soak test.

The end-to-end replay uses synthetic prices and explicit 0.001 taker fees and
records an exact net estimate of **8.58880 USD** for a one-unit synthetic trade.
It proves the pipeline's arithmetic and persistence, not market profitability.
Test records are cleaned up. Live deployment uses unknown fees and cannot emit
opportunities until explicit fee assumptions are provided.

### Issues discovered and resolved during validation

- Initial package install without its build environment failed due to absent
  setuptools; normal isolated editable installation succeeded.
- Sandbox DNS, localhost connections and Docker socket access were initially
  blocked. Authorized network access allowed actual exchange and PostgreSQL checks.
- Coinbase engine timestamps on queued updates can predate snapshot publication.
  Observations now consistently use the server envelope timestamp; engine timestamps
  are still parsed and validated.
- Coinbase current public sequence numbers span acknowledgements, heartbeats and
  book data. Tracking only level2 messages caused false gaps; tracking every
  sequenced frame resolves this and is covered by regression tests.
- Host-mounted monitoring configuration blocked container startup on this host.
  Configuration now ships inside monitoring images, removing that dependency.
- GitHub CLI's token is invalid. The connected GitHub tool provides repository
  access for publishing; no token was requested or copied into the project.

## Human acceptance

1. Follow README startup commands. Check `docker compose ps`; PostgreSQL should
   be healthy and app, Prometheus and Grafana running.
2. Check logs for `research_started` and the unknown-fee warning. Confirm metrics
   show observations for Kraken/Coinbase and BTC/USD/ETH/USD. Synchronization
   should settle to 1, with receive ages normally below the configured age gate.
3. Open Grafana at `http://localhost:3000/d/crossx-phase1`; confirm populated book
   rates, synchronization, age, reconnect and queue panels. Confirm Prometheus
   target health is up.
4. Run the full test commands in README with the test database URL, then the
   public probe with `--timeout 55 --duration 35` to check sustained continuity.
5. Verify unknown fees produce no live opportunities. Supply independently
   verified taker fee assumptions and recreate app. Absence of opportunities
   remains a valid result. Validate the recording path using the deterministic
   integration test rather than expecting a real profitable spread.
6. Restart app and confirm it obtains new snapshots before resuming detection.
   Stop PostgreSQL temporarily; app should fail and restart, then recover after
   PostgreSQL returns. Avoid this during any other local use of this database.
7. Stop with `docker compose stop` after collecting sufficient observations.
   Inspect database volume size before a longer collection run.

## Limits and acceptance status

The implemented scope is a research opportunity observer. It does not reserve
balances, simulate fills, maintain paper inventory or produce realised P&L. Repeated
signals share displayed liquidity. Fixed sizes and taker fees omit trading increments,
minimum orders, inventory allocation, transfer/rebalancing costs, latency, adverse
selection and GBP conversion. Data does not establish that £500–£1,500 is deployable
or profitable. No live profitability claim is made.

Only published top-25 depth is evaluated. Coinbase reconstructs full received
internal depth; very large snapshots can exceed the transport's 8 MiB message
limit, in which case the connection resets and detection fails closed. Clock skew
or conservative integrity validation can reduce data coverage. Observations append
without retention, so long runs require disk monitoring. Book and opportunity
writes are separate transactions; exactly-once recording is not promised.

No long-duration soak, fault injection against real exchanges, production security
review or exhaustive exchange outage testing has been completed. Dashboard
provisioning and APIs were checked; visual dashboard acceptance remains human work.
Dependency locks are reproducible, while Docker base-image tags may receive patches.
No merge to main is authorized or performed. CI is configured; its remote execution
status must be checked on the draft PR independently of the local results above.
