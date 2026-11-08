# Phase 1 operational acceptance

**Verdict: CONDITIONAL PASS.** The full 15-minute sustained observation and the
bounded shutdown/restart checks completed with recorded measurements. All four
books continued collecting with zero reconnects, integrity errors or unexpected
restarts. Unconditional acceptance is withheld: the run reproduced event-stale
observations and frequent full-queue bursts, and the fresh Grafana still accepted
its default administrator through the API. Retention/alerts and residual security
concerns remain open. Conditions are to resolve default-admin access, instrument
and profile event lag/queue saturation, and agree disk/retention/alerting controls
before longer or shared-host operation.

Reviewed implementation: `fae2e6c8d2138eae19e441eeafb3ea956114d098`
(`codex/phase-1-independent-review`). Acceptance branch:
`codex/phase-1-operational-acceptance`. No pull request was merged. Application,
pricing, adapter, migration and primary deployment source were not changed for
this acceptance exercise; changes are the isolated harness, evidence and report.

## Isolation and method

A standalone Compose definition, [ops/compose.acceptance.yaml](../ops/compose.acceptance.yaml),
created project `crossx-acceptance-20261107t2307`, four new containers, one new
network and three new volumes. No external volumes/networks, shared container
names, host mounts or existing service ports were used. Host ports were loopback
15432 (PostgreSQL), 18080 (app), 19091 (Prometheus), 13080 (Grafana).

The original `crossx-arbitrage-enginepublic` stack remained running. Before/after
snapshots compare its container IDs, image IDs, start times, restart counts,
network memberships, volume names, bindings and running state. The test never
stopped, recreated, queried or wrote its research database. The acceptance
application used a newly initialized PostgreSQL volume with the same Alembic
migration and app image source as the reviewed implementation.

Docker VM capacity and exact runtime versions are in
[environment.json](evidence/operational-acceptance/environment.json). The same VM
also hosted the original stack and other workloads. Resources were isolated by
Docker project, not by dedicated hardware or CPU/memory limits. Results therefore
include possible shared-host contention and collector overhead, and are not a
maximum-capacity benchmark.

The sampler used monotonic elapsed time and flushed JSONL every 15 seconds.
Metrics, PostgreSQL aggregate queries, container inspection and `docker stats`
were collected concurrently. Prometheus scraped every five seconds; its history
was retained separately. Monitoring provisioning/health checks ran every fourth
sample (roughly every minute). Shutdown and restart occurred only after the full
continuous measurement interval. No synthetic orders, exchange credentials,
paid infrastructure, fees assumed as zero, or execution capabilities were used.

## Measured window and results

Start **2026-11-07T23:09:38.301781+00:00**, end **2026-11-07T23:24:38.303837+00:00** (UTC; also Europe/London
on this date). Monotonic completed window **900.004906 seconds**.
There were **61 samples**, no collection errors, a maximum sample gap of
15.009 seconds and maximum collection cost of
2.703 seconds. The initial 2,968 warm-up rows are excluded below.

| Exchange / symbol | Added in timed interval | Mean observations/s |
| --- | ---: | ---: |
| coinbase BTC/USD | 13,167 | 14.63 |
| coinbase ETH/USD | 12,512 | 13.90 |
| kraken BTC/USD | 65,776 | 73.08 |
| kraken ETH/USD | 51,782 | 57.54 |
| **Total** | **143,237** | **159.15** |

The final timed database count was 146,205; metric counter
deltas totaled 143,253 (separate, concurrent snapshots).
Fifteen-second write-rate intervals ranged from
44.93 to 335.97 rows/s.

| Operational measurement | Result |
| --- | --- |
| Reconnects / integrity errors | 0 / 0 on each venue |
| Unexpected container restarts | 0 across all four services; one ID/start time each |
| Ready / synchronized / Prometheus up | 1 throughout all 181 five-second history points |
| Unknown-fee opportunity rows | 0 at every database sample |
| Queue depth | 0–128; full in 19/181 history points (10.5% of sampled points); returned to 0 |
| Mean persistence latency | 2.593 ms across 143,253 timed calls |
| Persistence p50 / p95 / p99 bucket bounds | ≤5 ms / ≤10 ms / ≤10 ms |
| Directly sampled maximum persisted receive age | 1.347 s |
| Monitoring/provisioning checks | 16/16 healthy; nine provisioned panels each |

Prometheus query-grid receive age peaked at 3.170 seconds; unlike a direct scrape,
that query includes time since the preceding scrape. Neither receive-age number
captures the event-age problem below.

| Service | Mean / max CPU % | Mean / peak memory MiB | First → last memory MiB |
| --- | ---: | ---: | ---: |
| app | 26.88 / 50.24 | 77.87 / 89.45 | 76.57 → 78.47 |
| postgres | 6.32 / 16.96 | 103.29 / 148.00 | 26.36 → 147.00 |
| prometheus | 0.24 / 5.37 | 40.43 / 50.55 | 50.16 → 40.84 |
| grafana | 0.68 / 3.22 | 312.46 / 428.60 | 322.30 → 284.20 |

| PostgreSQL measurement | Initial | Final | Growth |
| --- | ---: | ---: | ---: |
| Book table + indexes/TOAST, bytes | 3,399,680 | 158,294,016 | 154,894,336 (147.72 MiB) |
| Database bytes | 11,222,039 | 166,116,375 | 154,894,336 |
| Generated WAL bytes | 13,071,525 | 185,371,190 | 172,299,665 |

The measured ratio was 1081.4 book-storage bytes per added
observation. Constant-rate extrapolation gives **14.87 GB/day**
for books and **16.54 GB/day generated WAL**.
PostgreSQL committed-transaction statistics increased by
144,186, consistent with roughly one
transaction per book plus collector queries; those asynchronous statistics are
not an exact commit ledger.

### Event-age diagnostic (read-only, same completed receive-time window)

| Exchange / symbol | Rows in timestamp-filtered diagnostic | Event→receive mean / p95 / max seconds | Already >5 s old on receipt |
| --- | ---: | ---: | ---: |
| coinbase BTC/USD | 13,156 | 0.061 / 0.069 / 0.224 | 0 |
| coinbase ETH/USD | 12,504 | 0.061 / 0.065 / 0.254 | 0 |
| kraken BTC/USD | 65,690 | 0.501 / 2.295 / 5.862 | 300 |
| kraken ETH/USD | 51,780 | 0.224 / 1.186 / 5.858 | 81 |

All diagnostic records had 25 bids and 25 asks; none had an event/publication
time ahead of local receive time. The timestamp-filtered count differs from
snapshot count deltas because receipt and persistence boundaries differ. These
are exchange-observation/publication-to-local-receive differences, not an isolated
network RTT measurement; source clock offsets, upstream processing and buffering
can contribute.


Observation counts are **validated, persisted books**, not raw WebSocket frames
or proof that no upstream messages were delayed. The app has no separate ingress
counter. The database interval and metrics interval use concurrent reads rather
than one atomic snapshot; small count discrepancies are expected. Warm-up rows
before the first timed sample are excluded from interval deltas and reported
separately. Startup and deliberate post-test restarts are not counted as
unexpected restarts in the sustained interval.

Persistence mean uses the difference of histogram sum/count across the window.
Quantiles are reported as observed histogram bucket upper bounds, not exact
per-write percentiles. Failed calls would also contribute to that timer; no
failure was inferred from a short timer alone. CPU figures are sampled Docker
percentages (100% is approximately one CPU), not a continuous CPU profile. Docker
memory usage is the daemon's reported working-memory figure, not a leak proof.

PostgreSQL table size includes indexes and TOAST; database size also includes
other database objects. WAL bytes are generated bytes, not retained WAL disk
usage. Daily figures are linear extrapolations from **this measured window**,
not a completed day-long test, and exclude future market-rate changes and most
other services' storage. Storage and input-rate peaks matter more than a single
average when planning collection.

## Monitoring, unknown fees, shutdown and recovery

Both explicit stops exited 0, in **0.818 s** and
**0.740 s**, within the 15-second Docker bound. No
`shutdown_drain_timeout` warning appeared. The stopped count was
148,520 and remained identical
for the three-second stability check. After restart readiness at
2026-11-07T23:25:08.271413+00:00, row count increased from
148,680 to
150,431 over the additional
30-second collection check, with increases on all four pairs. Opportunity count
remained zero. Container identity was preserved across the intentional start;
no other service was restarted during this check.

The lifecycle log contained exactly two `research_started` entries and four
unknown-fee warnings (two venues on each intentional start). Original-stack
identity/lifecycle comparison passed before and after disposable cleanup. Only
the acceptance project's four containers, one network and three new volumes were
removed; remaining acceptance resource counts are zero. The original stack is
still running unchanged.


The acceptance Compose file explicitly supplies null maker/taker fees on both
venues. Runtime environment inspection, both startup warnings and zero database
opportunity rows verify disabled opportunity generation while books accumulate.
This test does not exercise live positive-fee opportunity detection or assert
profitable market conditions; deterministic arithmetic tests cover that path.

The timed interval included no deliberate outage. Afterwards the acceptance app
received SIGTERM with a 15-second Docker stop bound, the database row count was
checked stable while stopped, and the same container was started again using the
same disposable volume. Both symbols on both venues had to resume and increase
for an additional 30 seconds. The app was stopped again and its logs checked for
any drain-timeout warning. These are cooperative shutdown/restart checks, not
power-loss or crash-recovery durability tests.

## Confirmed findings and readiness gaps

### Medium — default Grafana administrator remains usable

A read-only check against the **disposable** Grafana at 23:14:19 UTC returned
HTTP 403 for anonymous `GET /api/admin/settings` and HTTP 200 for the same request
using the default `admin/admin`. Only statuses were saved; response settings
were deliberately discarded. No account, setting or permission was changed.
Evidence: [security-probe.json](evidence/operational-acceptance/security-probe.json).

The reviewed Compose configuration hides the login form and enables anonymous
Viewer access but does not disable basic authentication or initial admin creation.
Anonymous Viewer-only access therefore does not mean the instance has no working
administrator credentials. Loopback binding constrains exposure; no remote exploit
is claimed. [Grafana documents the default administrator and first-run password](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/),
and [distinguishes hidden login forms from disabled basic authentication](https://grafana.com/docs/grafana/latest/setup-grafana/configure-access/configure-authentication/grafana/).

Reproduce only against a fresh disposable acceptance instance:

```sh
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:13080/api/admin/settings
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' -u admin:admin \
  http://127.0.0.1:13080/api/admin/settings
# Observed: 403, then 200. These are Grafana development defaults, not exchange keys.
```

Recommendation: deliberately disable basic authentication/initial admin creation
for an immutable anonymous Viewer deployment, or provision/rotate nondefault admin
credentials and retest the API. A hidden form is not that control. This acceptance
PR documents the confirmed defect and does not change either running deployment.

### Medium — storage, burst capacity and operational controls

The queue was full (128) at **19 of 181** five-second history points, with
subsequent recovery. This is measured burst saturation, not a proof of lost
messages or a maximum sustained throughput limit. The single consumer performs
an individual transaction for every persisted book; this is a candidate bottleneck
for profiling, not a root-cause determination from average CPU alone.

**Confirmed data-quality/observability gap:** 300 Kraken BTC and 81 Kraken ETH
observations were already over the configured five-second age limit when received;
maximum age was 5.862 seconds. Meanwhile every synchronization/readiness point was
1 and directly sampled receive-age stayed below 1.35 seconds. The detector checks
`observed_at` as well as `received_at`, so these records must be excluded from
opportunities once evaluated; they are not evidence of unsafe execution. However,
the receive-age dashboard cannot expose this stale-event population and there is
no event-lag or stale-rejection counter. Buffering/backpressure is consistent with
the queue peaks, but this test does not isolate whether application, library,
network or exchange buffering caused each delay.

Reproduce the measured defect using the exact read-only SQL saved in
[database-diagnostics.json](evidence/operational-acceptance/database-diagnostics.json)
or, before disposable cleanup, run:

```sh
uv run --frozen python -m ops.capture_acceptance_database \
  docs/evidence/operational-acceptance/observations.jsonl \
  --project crossx-acceptance-20261107t2307 \
  --out /tmp/crossx-event-age-reproduction.json
```

The stale records, queue saturation and approximately 14.87 GB/day book-storage
extrapolation warrant event-age/rejection metrics, bounded-load profiling and a
storage plan before longer collection. No message-processing or data-retention
architecture was changed by this acceptance PR.


No retention, disk-size metric/exporter, alert rules, disk exhaustion protection,
service resource limits or event-loop-lag measurement exists in the reviewed
configuration. Retention or batching/depth/cadence changes need explicit research
requirements and separate review; no automatic deletion or unrelated architectural
change was introduced here. Recommended next work is a disk budget and retention
policy, sustained queue-age/lag profiling, and alerts for stale books, failed
scrapes, queue saturation, write latency, restart loops and free disk.

### Missing measurements in the platform

The harness supplies container resources, database size/WAL growth and restart
identity checks that the application dashboard does not expose. The platform lacks
separate raw/validated ingress, rejection-by-reason, queue residence time, transport
buffer delay, source-generation invalidation counts and exact abandoned-row counts.
Consequently zero reconnects and bounded queue size do not prove lossless ingestion
or continuously executable data. A 15-second Docker sample and five-second scrape
can miss shorter peaks. Readiness indicates a running consumer/database startup,
not a guarantee of fresh books; feed health and receive ages must be checked too.

### Security and research limits carried forward

The independent review's [scan evidence](SECURITY_SCAN_SUMMARY.json) still records
residual HIGH image matches and one CRITICAL match in an embedded PostgreSQL-image
utility; reachable exploitability was not established. Those scans were not rerun
and must not be represented as a clean security bill for this exercise. Python and
PostgreSQL base tags remain mutable; exact image IDs are recorded for this run.
Local DB development credentials and unauthenticated observability are unsuitable
for an untrusted shared host even when published ports bind to loopback.

No balances, paper fills, capital reservation, GBP conversion, latency-aware fills,
rebalancing costs or realised P&L are validated here. Repeated book observations
share liquidity. This report makes no production-readiness, trading-feasibility or
profitability claim and does not claim a 24-hour soak.

## Evidence and reproduction

Committed evidence is under `docs/evidence/operational-acceptance/`:

- `observations.jsonl.gz`: all 61 raw timed snapshots plus start/end markers,
  including per-pair metrics, SQL aggregates, resources and container identities.
- `prometheus-history.json.gz` and `prometheus-summary.json`: 181 five-second
  query-grid points per series for health, queue, receive age and feed errors.
- `summary.json`: reproducible interval deltas, histogram bounds and resource statistics.
- `database-diagnostics.json`: exact read-only SQL and event-age/depth results.
- `lifecycle.json`: stop/start timestamps, exit status, runtime fee configuration,
  database stability/resumption, application logs and preservation check.
- `isolation-before.json`, `isolation-after.json`, `environment.json`,
  `security-probe.json` and `manifest.json`: scope, identities, versions, cleanup,
  read-only admin-API reproduction and SHA-256 integrity evidence.

Gzip archives preserve the raw bytes; uncompressed originals were retained locally
and excluded from Git. `manifest.json` includes hashes for both raw and archived
versions. Summarization supports the archive directly:

```sh
uv run --frozen python ops/summarize_acceptance.py \
  docs/evidence/operational-acceptance/observations.jsonl.gz
```

A developer-check rerun passed Ruff and strict mypy; pytest executed **87 passed,
3 skipped**. The three database/fault integration tests were intentionally not
pointed at the measured dataset. The actual sustained runtime, diagnostics and
lifecycle checks above supply this task's operational evidence. No earlier CI or
90-test result is being represented as newly executed here.


The named acceptance ports must be free. Always supply a **new** project name
beginning `crossx-acceptance-`; do not run the repository's default Compose stack
for this procedure. The standalone acceptance definition creates project-scoped
names and volumes. To change ports, export ACCEPTANCE_DB_PORT,
ACCEPTANCE_APP_PORT, ACCEPTANCE_PROM_PORT and ACCEPTANCE_GRAFANA_PORT and pass the
matching ports to the scripts. Save outputs to a new directory on every run.

```sh
uv sync --frozen --extra dev
uv run --frozen ruff check .
uv run --frozen mypy src
uv run --frozen pytest -q
# Actual local result: 87 passed, 3 integration tests skipped; no existing DB targeted.

# Replace this name on a new run. These were the exact project/build commands used:
docker compose --project-name crossx-acceptance-20261107t2307 \
  --file ops/compose.acceptance.yaml config --quiet
docker compose --project-name crossx-acceptance-20261107t2307 \
  --file ops/compose.acceptance.yaml up -d --build
# Wait for PostgreSQL health, all four app book counters, Prometheus target up,
# and Grafana /api/datasources/uid/prometheus/health status OK before starting.
uv run --frozen python ops/observe_acceptance.py \
  --project crossx-acceptance-20261107t2307 --duration 900 --interval 15 \
  --out docs/evidence/operational-acceptance/observations.jsonl
uv run --frozen python ops/summarize_acceptance.py \
  docs/evidence/operational-acceptance/observations.jsonl
uv run --frozen python ops/capture_acceptance_prometheus.py \
  docs/evidence/operational-acceptance/observations.jsonl \
  --out docs/evidence/operational-acceptance/prometheus-history.json
```

The original-stack isolation snapshot uses `containers()` from
`ops.observe_acceptance` and records only Docker identity/lifecycle/volume/network
metadata, not application environment secrets. Before lifecycle testing, capture
an equivalent `isolation-before.json` with `original` and `acceptance` container
lists. The committed snapshot proves the resources used here; it cannot substitute
for the identities of a newly created project on another run.

```sh
uv run --frozen python -m ops.check_acceptance_lifecycle \
  --project crossx-acceptance-20261107t2307 \
  --original-snapshot docs/evidence/operational-acceptance/isolation-before.json \
  --out docs/evidence/operational-acceptance/lifecycle.json
# Explicit disposable project only; never substitute the original project here:
docker compose --project-name crossx-acceptance-20261107t2307 \
  --file ops/compose.acceptance.yaml down --volumes
```

The lifecycle script is scoped to this workstation's original project name for
its read-only preservation check; adapt that name and take a fresh snapshot on
another workstation. Archive raw evidence before removing the disposable volumes.
Do not merge this PR or either earlier PR automatically.
