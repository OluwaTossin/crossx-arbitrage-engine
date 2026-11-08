# Phase 1 independent review

Review date: 7 November 2026, Europe/London. Reviewed PR #1 and base commit
`2870e77c5e8bcfe3a48d8b1c64ec0645f0faf574` on `codex/phase-1`.
Review branch: `codex/phase-1-independent-review`. No merge is authorized.

## Scope and method

The lead reviewed application concurrency, persistence, shutdown, configuration,
observability, architecture and CI. Three independent reviewers covered exchange
integrity, financial arithmetic, and DevSecOps/container security. File ownership
was separated before edits. Existing passing tests were treated as coverage,
not evidence that untested paths were correct.

Confirmed application defects below were reproduced with failing regressions
before their fixes. Image and CI findings use scanner/configuration evidence;
no vulnerability exploit was attempted. Fault injection used fake feeds/stores
and a newly created `crossx_independent_review` PostgreSQL database. Existing
research observations and the running original stack were not altered. Container
builds change local image tags; existing running containers keep their original
images until explicitly recreated.

## Severity-ranked findings

Severity reflects impact and remediation priority in this local research system.
Scanner severity is reported separately from demonstrated application impact.
No Critical application exploit or correctness defect was confirmed.

| ID | Severity | Status | Finding and references |
| --- | --- | --- | --- |
| R1 | High | Fixed | A stalled persistence call could hang the only consumer indefinitely while readiness stayed 1. No storage deadline existed. [app.py](../src/crossx/app.py), [config.py](../src/crossx/config.py), `tests/test_runtime_review.py::test_stalled_storage_fails_within_configured_deadline`. |
| R2 | High | Partially remediated; residual matches remain | Original monitoring images contained known HIGH/CRITICAL vulnerable package versions. Updated verified releases are digest pinned. [monitoring Dockerfiles](../monitoring/), [scan evidence](SECURITY_SCAN_SUMMARY.json). Reachable exploitation was not established. |
| R3 | Medium | Fixed | Closing an adapter generator did not clear its synchronized generation; cancellation while blocked on the queue also failed to explicitly close the suspended generator. [base.py](../src/crossx/adapters/base.py), [app.py](../src/crossx/app.py), `tests/test_adapter_review.py`, runtime backpressure regression. |
| R4 | Medium | Fixed within bounded grace | SIGTERM canceled accepted and in-flight observations immediately instead of finishing writes. [app.py](../src/crossx/app.py), `tests/test_runtime_review.py::test_sigterm_drains_accepted_observations_or_bounds_stall`. |
| R5 | Medium | Fixed | Dividing depth notionals into repeating VWAPs and multiplying back manufactured a positive signal at exact breakeven. [intelligence.py](../src/crossx/intelligence.py), `tests/test_financial_review.py`. |
| R6 | Low | Hardened | Workflow lacked an explicit least-privilege token bound and checkout retained credentials by default. [ci.yml](../.github/workflows/ci.yml). Actual inherited write access was not established. |

### R1 — bounded storage failure and recovery

The original `observe()` awaited `save_book()` and `save_opportunity()` without
an application deadline. An indefinitely waiting fake store left the observation
task pending beyond the regression's independent 200 ms watchdog. The regression
failed with `storage stall leaves consumer hanging indefinitely`. In the actual
runtime this blocks its single consumer, eventually fills its queue and leaves
readiness green because no exception reaches the task group.

Persistence and initialization now have a positive, finite configurable deadline,
default 10 seconds (`CROSSX_PERSISTENCE_TIMEOUT_SECONDS`). Timeout propagates,
allowing the existing task group failure/Compose restart behavior. This is
cooperative asyncio cancellation, not an unconditional OS-level execution deadline.
It cannot interrupt arbitrary synchronous work or cancellation-suppressing code.

The real PostgreSQL fault regression holds an exclusive table lock in the separate
review database, starts a book insert with a 50 ms deadline, observes TimeoutError,
releases the lock, checks no partial observation remains, then proves the pool can
persist a subsequent book. An independent two-second watchdog bounds this test.
The test locks only an explicitly supplied `CROSSX_FAULT_TEST_DATABASE_URL`;
never point that variable at a research/live database.

### R2 — vulnerable monitoring images and residual security risk

Trivy 0.74.0, advisory database updated at `2026-11-07T18:32:58Z`, reported:

| Remote base scanned | Unique HIGH/CRITICAL advisory IDs | Package matches | CRITICAL package matches |
| --- | ---: | ---: | ---: |
| Grafana 11.3.1, original | 90 | 157 | 9 |
| Grafana 13.3.0, replacement | 7 | 54 | 0 |
| Prometheus 3.0.1, original | 57 | 118 | 4 |
| Prometheus 3.16.0, replacement | 1 | 2 | 0 |
| Python 3.12-slim, current remote digest | 13 | 49 | 0 |
| PostgreSQL 16-alpine, current remote digest | 26 | 26 | 1 |

Package matches repeat across binaries; unique advisory IDs deduplicate those
matches, not independent exploitable paths. Monitoring updates reduce known
exposure but **do not produce a clean security scan**. Python OS findings remain;
PostgreSQL image matches were in the embedded Go standard library in `gosu`, not
a demonstrated PostgreSQL server exploit. That includes one CRITICAL match whose
reachable impact in this deployment is unverified. Do not infer that PostgreSQL
has a proven remote Critical vulnerability from this scan.

Registry scans identify the exact digests in `SECURITY_SCAN_SUMMARY.json`, not
necessarily images already running on this machine. Trivy flagged incomplete
Alpine lifecycle coverage for replacement images. Only HIGH/CRITICAL severities
were requested. The Python dependency scan detected `uv.lock` and reported zero
matches at those severities; that is not a full source/security audit.

Official release/advisory references and exact scan commands are in the JSON
summary. Replacement monitoring images built successfully. Disposable containers
verified Prometheus readiness and `promtool check config`, Grafana database health,
all nine provisioned panels, and successful datasource communication between the
new versions. Temporary monitoring containers and network were removed.

### R3 — deterministic feed closure

After the original adapter emitted a valid snapshot, `await stream.aclose()`
closed its socket but left `synchronized_symbols == {'BTC/USD'}`, book state and
generation intact. The regression failed on the expected empty set. `GeneratorExit`
now follows the same invalidation path as cancellation.

A second failed regression filled a size-one queue and canceled `produce()` while
it awaited its second `queue.put()`. The retained stream remained open and
synchronized after the producer returned. `produce()` now uses `contextlib.aclosing`
so stream closure completes before producer exit, rather than relying on garbage
collection/finalization timing. The regression verifies bounded backpressure and
cleared synchronization/incremented generation after cancellation. Adapter stream
annotations now describe the actual closable async generator contract.

### R4 — graceful shutdown without an unbounded wait

A fake feed queued three observations while a cooperative store was writing the
first. Triggering the registered SIGTERM callback on the original runner persisted
**zero**, failing `SIGTERM discarded accepted/in-flight observations`.

Shutdown now clears readiness, cancels/closes producers, and drains accepted work
for a configurable five-second grace (`CROSSX_SHUTDOWN_TIMEOUT_SECONDS`). The
regression verifies all three observations are stored. A separate stalled-store
case verifies bounded exit and the `shutdown_drain_timeout` warning. Grace expiry
increments `crossx_shutdown_timeouts_total`; the log is the reliable shutdown
record because a final metric increment might not be scraped before process exit.

This does not promise lossless shutdown. Work still pending after the grace is
abandoned, and abrupt process death still loses the queue. If increasing the
configured grace, increase Docker's stop timeout beyond it. Adapter closure
invalidates queued generations for detection; historical book persistence can
continue without emitting opportunities from a disconnected feed.

### R5 — use depth notionals directly for money

Synthetic quantity 3, buy depth cost 100, sell depth proceeds 101, buy taker fee
1%, sell taker fee 0% is exactly breakeven. The original detector returned an
opportunity with `net_profit=1.01E-48`, even at Decimal precision 50. Its division
into repeating VWAPs followed by multiplication introduced the residual.

The fix accumulates depth notionals directly for gross profit and both fees;
VWAP is calculated separately for reporting. The breakeven regression failed on
the original and passes after the fix. Finite Decimal precision remains a numerical
policy; this change removes the demonstrated unnecessary round-trip division.

### R6 — CI credential scope

The original workflow inherited repository/organization token permissions and
used checkout v4's default persisted authentication. The workflow now declares
`permissions: {contents: read}` and sets `persist-credentials: false`. This is
configuration-verifiable preventive hardening, not evidence that a write token
or secret was exploited. Workflow behavior remains covered by remote CI.

## Review conclusions by area

- **Exchange integrity:** Kraken's formatting/truncation/top-ten checksum logic
  and existing official checksum vector agree with its published specification.
  Coinbase sequence handling, snapshot ordering and public feed continuity passed
  deterministic and bounded live checks. No additional wire-protocol defect was
  confirmed. This does not establish behavior under every exchange outage.
- **Freshness:** both event/publication and receive age, inter-venue skew,
  synchronization and cached generation are checked. Heartbeats do not refresh
  book age. Local wall-clock jumps can conservatively suppress signals; their
  long-duration effects were not fault-injected.
- **Concurrency:** queue remains bounded at 128; the new size-one test proves
  backpressure/cancellation behavior. No throughput ceiling or full-message-rate
  stress capacity has been established. Slow storage can still reduce coverage.
- **Financial:** unknown taker fees fail closed; both legs consume available
  published depth and pay their own fee. No automatic FX conversion occurs; only
  USD pairs are configured. Repeated observations can share liquidity and are
  research signals, not independent trades or realised P&L.
- **Security:** reviewed source exposes only public subscriptions, with no order
  submission, exchange account/trading credentials, withdrawals, leverage or
  derivatives. Published Compose ports bind to loopback. App runs as UID 10001;
  Grafana anonymous access is Viewer-only. Local development DB credentials and
  same-host access remain inappropriate for a shared/untrusted deployment.

## Remaining risks and recommendations

These are not claimed as reproduced application failures unless stated.

1. **Medium operational design gap, confirmed configuration:** observations append
   without retention and Prometheus has no alert rules. Disk exhaustion was not
   induced. Measure storage/day and define retention/backup and alerts for stale
   feeds, readiness, queue/persistence latency, disk and restart loops before
   prolonged collection. Do not silently delete existing research data.
2. **Residual container advisory matches:** retain digest/advisory review and
   refresh scans as upstream fixes appear. Dependency locks are not a security
   guarantee. Python/PostgreSQL tags remain mutable; these were not replaced
   merely to hide scanner findings or introduce an unverified image variant.
3. **Separate writes, confirmed design limit:** book and opportunity writes are
   separate transactions. A later signal-write failure leaves its book committed;
   crash/restart is not exactly once. Atomicity/idempotency requirements need a
   deliberate data-model design before trading-like accounting.
4. **Historical generation metadata:** old queued books are persisted with their
   original timestamps and excluded from detection, as existing tests specify.
   Rows do not store connection generation or invalidation reason. Downstream
   historical research must not assume these rows represent continuously live
   executable quotes. Add provenance if that analysis becomes a requirement.
5. **Research model limits:** only top-25 published levels, no cash/inventory
   reservation, no realistic fills, trading increments, minimum orders, funding,
   settlement/rebalancing, latency or GBP costs. Duplicate observations must not
   be aggregated into a profit claim. Maker fees do not imply maker execution.
6. **Unperformed testing:** no 24-hour soak, production penetration test, maximum
   throughput benchmark, network blackhole/cancellation-suppression test or real
   exchange fault injection completed. This report declares neither production
   readiness nor profitability.

## Executed validation

Environment: macOS arm64, Python 3.12.15, Docker server 29.8.2. Public-network,
Docker and localhost database tests used authorized elevated execution because
sandbox access is restricted.

| Executed check | Result |
| --- | --- |
| Focused regressions before changes | Stalled write, immediate-shutdown loss, explicit stream-close state, backpressured stream leak and breakeven rounding each failed against the relevant original code |
| Full pytest with isolated PostgreSQL and fault URLs | **90 passed**, including three PostgreSQL integration tests |
| `ruff check .` | Passed |
| `mypy src` | Passed, strict, 11 source files |
| Alembic upgrade on new review database | Passed |
| `docker compose config --quiet` | Passed |
| `docker compose build` | App, Grafana and Prometheus builds passed |
| Dependency/base-image vulnerability scans | Completed; results and limits above and in JSON evidence |
| Fresh monitoring container smoke | Readiness, configuration validation, dashboard and datasource checks passed |
| Disposable reviewed application container | Metrics HTTP 200; all four pairs persisted (Kraken BTC 3440 / ETH 3244; Coinbase BTC 1413 / ETH 1085), zero reconnects/integrity failures, queue 0; `docker stop -t 15 crossx-review-app` exited 0; temporary container removed |
| Public `--timeout 55 --duration 35` probe | Exit 0; Kraken BTC 3137 / ETH 1065; Coinbase BTC 459 / ETH 425 observations; zero reconnects/integrity failures |

Remote CI is independent of these local results; inspect the new draft PR's
checks. The original PR's green run does not substitute for review-branch checks.

## Exact human acceptance commands

Start in the repository on `codex/phase-1-independent-review`. No exchange keys
or live orders are involved. Existing research data should not be the test target.

```sh
uv sync --frozen --extra dev
uv run --frozen ruff check .
uv run --frozen mypy src
uv run --frozen pytest -q
# Without database URLs, the three integration tests intentionally skip.
docker compose config --quiet
docker compose build
uv run --frozen python scripts/check_connectivity.py --timeout 55 --duration 35
```

For a separate disposable database on localhost port 55432 (choose another free
port/name if occupied):

```sh
docker run --rm -d --name crossx-review-db \
  -e POSTGRES_USER=crossx -e POSTGRES_PASSWORD=crossx -e POSTGRES_DB=crossx \
  -p 127.0.0.1:55432:5432 postgres:16-alpine
# Wait until this readiness check exits 0 before migrating:
docker exec crossx-review-db pg_isready -U crossx -d crossx
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:55432/crossx \
  uv run --frozen alembic upgrade head
CROSSX_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:55432/crossx \
CROSSX_FAULT_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:55432/crossx \
  uv run --frozen pytest -q
# Only this explicitly disposable container/data is removed:
docker stop crossx-review-db
```

The equivalent commands executed in this review used the newly created
`crossx_independent_review` database on the existing local PostgreSQL server at
port 5432, never the original `crossx` research database.

To accept the updated local deployment, after preserving any desired current
research session, run `docker compose up -d --build`; this recreates services with
the reviewed images and preserves the PostgreSQL named volume. Confirm four
exchange/symbol book counters and fresh receive times at `http://localhost:8000/metrics`,
Prometheus target health at `http://localhost:9090/api/v1/targets`, and the nine
Grafana panels at `http://localhost:3000/d/crossx-phase1`. Fees are unknown by
default, so absence of opportunities is expected. Run `docker compose stop -t 15`
and inspect app logs for orderly completion or the explicit drain-timeout warning.
Do not sum repeated signals as realised profit, and do not merge either PR automatically.
