# Phase 1.2 operational hardening

Recommendation: **CONDITIONAL PASS for local research**. Correctness and security regressions pass, with substantially improved bounded burst throughput. Repeat the original 15-minute acceptance and a longer supervised run before unattended use. This work does not establish profitability or production readiness. No pull request was merged.

## Scope and preservation

Base: `codex/phase-1-operational-acceptance`, commit `b2b09eab0b041edbe486c5bee95c54d069bf3e2d`. Only disposable project `crossx-acceptance-hardening-20261107` was exercised, using host ports 16432, 18081, 19092 and 13081. Storage telemetry adds no host port. Existing acceptance documents/evidence and original research observations were not modified. No original-stack deployment was performed. Disposable databases contain appended synthetic and public observations; no volume or observation deletion is part of this change.

## Confirmed findings and fixes

| Severity | Finding and evidence | Change |
|---|---|---|
| High | Prior acceptance proved `admin/admin` could access Grafana admin API. | `monitoring/grafana/grafana.ini` disables basic auth, login form and initial admin creation; anonymous role remains Viewer. HTTP regression verifies read-only dashboard access and admin denial. |
| Medium | Per-observation transactions impose sustained round-trip overhead. Controlled 3,000-row PostgreSQL benchmark measured 544 rows/s individually versus 6,703 rows/s in batches of 32; serialization took only 0.143s. | `Database.save_books` and consumer immediately drain up to 32 queued items without waiting to fill a batch. Capacity remains 128. Transaction atomicity, Decimal strings and post-await generation checks are tested. |
| Medium | Existing telemetry conflated clock freshness with waiting and did not expose queue residence or transaction batching. Prior run had 381 Kraken observations already >5s old on receive. | Separate event-clock age, reconstruction duration, producer delay, admission wait, queue residence, transaction latency and per-book commit latency. Stale observations remain historical evidence but cannot seed opportunities. |
| Medium | Unbounded research history lacks local disk-capacity warnings. | Read-only storage exporter, configured budgets and alert rules; optional sampling and read-only retention planner. No automatic deletion. |

Source references: `src/crossx/app.py` (produce, consume, persistence and detection), `src/crossx/db.py` (atomic batch inserts and read-only queries), `src/crossx/observability.py`, `src/crossx/storage.py`, `monitoring/alerts.yml`, `monitoring/grafana/dashboards/crossx.json`. The dashboard now has 25 panels. CI executes Grafana HTTP security regression and Prometheus rule tests.

### Queue root cause and controlled comparison

`ops/benchmark_persistence.py` appends pre-serialized depth-25 observations to an isolated PostgreSQL database. Transaction count, rather than serialization alone, explains the measured bottleneck. `ops/stress_hardening.py` independently drives both the base consumer (loaded with `git show`) and current consumer with 4,096 observations against the same PostgreSQL instance. All source frames are modeled available at time zero; a 30-second watchdog bounds each phase. Results are in `docs/evidence/phase1-2/stress.json`.

| Metric | Base | Hardened |
|---|---:|---:|
| Throughput, rows/s | 489.70 | 5,223.09 |
| Duration, seconds | 8.364 | 0.784 |
| Transactions | 4,096 | 128 |
| Queue capacity / peak | 128 / 128 | 128 / 128 |
| Mean residence, ms | 258.04 | 23.68 |
| p95 residence, ms | 364.65 | 29.69 |
| Mean admission wait, ms | 1.977 | 0.173 |
| Mean per-book commit latency, ms | 2.004 | 5.747 |
| Event age >5s on modeled receive | 1,647 | 0 |

The burst still fills the bounded queue. Higher batch commit latency is explicitly reported; it is not divided by batch size. Lower residence and reduced transactions explain overall improvement. Measurements share a local Docker host with the original running stack and are not hardware-independent guarantees. Sustained arrivals above service capacity must still backpressure.

### Event freshness interpretation

Exchange event timestamps are preserved. Event-to-receive age includes exchange clock skew, exchange-side delay, network/WebSocket buffering and delayed application reads. It is **not a direct network transport measurement**. Reconstruction timing measures JSON decoding and adapter processing; producer delay measures receive-to-producer handoff; admission and residence separately measure queue waiting; commit timing measures persistence. One-way network transit cannot be isolated without reliable remote clocks and frame arrival instrumentation.

The controlled burst reproduces a local mechanism for old-on-receive events: blocking subsequent reads behind persistence. It does not prove all previous 381 events had that cause. Kraken checksum/reconstruction algorithms are unchanged; existing checksum precision and corruption tests continue to execute. Fixed-label rejection counters distinguish generation, unsynchronized, future timestamp, stale event and stale receive. Detection checks source generation after database awaits. Optional sampling support commits also recheck generation and recompute freshness before recording a signal; a deterministic regression reproduced and closed that race during implementation.

## Public feed evidence

Continuous measured window: **2026-11-07 23:50:13.422 UTC to 23:53:13.426 UTC**, 180.004 seconds, 13 samples. This is a bounded three-minute run, **not another 15-minute acceptance or a 24-hour soak**. The generic summary's `measurement_complete:false` deliberately reflects its 900-second criterion.

| Pair | New persisted observations |
|---|---:|
| Coinbase BTC/USD | 2,508 |
| Coinbase ETH/USD | 2,552 |
| Kraken BTC/USD | 17,322 |
| Kraken ETH/USD | 8,387 |

30,769 rows total, 170.93/s mean; sampled interval rates 68.87–385.30/s. SQL counts and metric counts differ slightly because sequential collection is not an atomic snapshot. Queue sampled maximum 5 (15-second sampling can miss short peaks); mean admission 0.008ms, residence 3.614ms, transaction 3.840ms, per-book commit 4.984ms. Mean observed batch size 3.13. No persistence failures, reconnections, integrity errors, stale-event rejections or unexpected restarts occurred within this measured window. All four books were synchronized and readiness remained 1 at sampled points. Unknown fees remained configured; opportunities stayed zero while collection continued.

Read-only SQL diagnostics restricted to exact receipt timestamps found zero events >5s old. Kraken BTC/ETH event-to-receive p95 was 40.89/49.16ms (maximum 94.42/96.31ms); Coinbase BTC/ETH p95 71.91/64.67ms. App CPU mean/max 20.80/27.52%; maximum memory 81.95MB; PostgreSQL CPU mean/max 5.76/11.62%, maximum memory 135.37MB. Full per-container measurements and immutable image identifiers are in `public-summary.json` and raw samples.

This timed image preceded a final optional-sampling generation guard change. Sampling was disabled (default zero), so that changed path was not exercised in this window. The final source is covered by the full regression suite and a separately recorded public adapter smoke test. `public-preinitialization.jsonl` is an earlier interrupted sampler, not part of these statistics.

## Grafana and monitoring verification

`security.json` records anonymous dashboard HTTP 200, anonymous admin HTTP 403 and `admin/admin` admin HTTP 403. Automated HTTP regression additionally checks dashboard `canSave:false` and `canAdmin:false`. Both Prometheus targets (`crossx`, `crossx-storage`) scrape successfully; datasource health is OK and dashboard provisioning yields 25 panels. Existing Grafana data volumes were not migrated or tested in place; the new basic-auth denial is baked into configuration, and the original stack still needs a separately approved deployment.

Eleven alert rules cover service readiness, event-clock freshness, stale rejection, sustained queue saturation/residence, persistence failures, storage telemetry, free space, budget proximity, daily growth and predicted exhaustion. Promtool validates configuration and seven deterministic firing scenarios. There is no Alertmanager or external notification delivery. Alerts are visible in local Prometheus; operators must monitor them. Labels use only bounded exchange/symbol/reason/operation dimensions.

## Disk budget and retention

The original run extrapolated 14.87GB/day of book data and 16.54GB/day WAL generation. This run grew book/database storage by 33,660,928 bytes in 180.004 seconds: **16.16GB/day (15.05GiB/day)**, approximately 1,094 bytes per observation. WAL generation extrapolated to 17.39GB/day; generated WAL is not necessarily retained WAL. Rates vary with market activity and allocated relation pages.

Default warning budgets: **20GiB/day book growth**, **30GiB dataset size**, plus free-space warning below 5GiB and predictive 24-hour dataset exhaustion. These are warnings, not quotas. A 30GiB dataset budget holds roughly two days at this measured rate, before other Docker data and headroom. Docker filesystem free space is shared; statvfs is not a per-volume quota. Metrics do not sum all Grafana/Prometheus/image storage or guarantee host free space. Do not allocate a long unattended run on these defaults without capacity review.

`CROSSX_BOOK_SAMPLE_INTERVAL_SECONDS=0` preserves every book. A positive interval samples routine history per venue/symbol while evaluating every incoming validated book and retaining current supporting books before signal persistence. Supporting evidence can add rows beyond the nominal sampling rate. Sampling changes historical resolution and should be recorded with each experiment; it is unsuitable for lossless replay. `CROSSX_RETENTION_DAYS` drives a **read-only eligibility count** through `python -m crossx.storage --plan`. Automatic expiry remains disabled. Proposed manual policy: export immutable evidence, verify checksums and replayability, then obtain explicit authorization for any archival/deletion operation. This PR contains no deletion command or automatic maintenance job.

## Executed validation and reproduction

Run only against a uniquely named disposable environment; do not reuse original ports or run bare Compose lifecycle commands against the original project.

```sh
export ACCEPTANCE_DB_PORT=16432 ACCEPTANCE_APP_PORT=18081
export ACCEPTANCE_PROM_PORT=19092 ACCEPTANCE_GRAFANA_PORT=13081
alias acceptance='docker compose -p crossx-acceptance-hardening-20261107 -f ops/compose.acceptance.yaml'
acceptance config --quiet
docker compose config --quiet
acceptance up -d --build
# Create a separate empty integration database once; never use existing research data.
acceptance exec -T postgres createdb -U crossx crossx_unit
.venv/bin/ruff check .
.venv/bin/mypy src
CROSSX_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:16432/crossx_unit \
CROSSX_FAULT_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:16432/crossx_unit \
CROSSX_TEST_GRAFANA_URL=http://127.0.0.1:13081 .venv/bin/pytest -q
.venv/bin/python ops/benchmark_persistence.py --database-url postgresql+asyncpg://crossx:crossx@localhost:16432/crossx --rows 3000
.venv/bin/python ops/stress_hardening.py --database-url postgresql+asyncpg://crossx:crossx@localhost:16432/crossx
.venv/bin/python ops/observe_acceptance.py --project crossx-acceptance-hardening-20261107 --duration 180 --interval 15 --app-port 18081 --prom-port 19092 --grafana-port 13081 --out /tmp/hardening-observations.jsonl
.venv/bin/python ops/summarize_acceptance.py /tmp/hardening-observations.jsonl
.venv/bin/python -m ops.capture_acceptance_database /tmp/hardening-observations.jsonl --project crossx-acceptance-hardening-20261107 --out /tmp/hardening-database.json
.venv/bin/python scripts/check_connectivity.py --timeout 55 --duration 35
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:16432/crossx_unit CROSSX_RETENTION_DAYS=1 .venv/bin/python -m crossx.storage --plan
acceptance run --rm --no-deps --entrypoint /bin/promtool --workdir /etc/prometheus prometheus check config /etc/prometheus/prometheus.yml
acceptance run --rm --no-deps --entrypoint /bin/promtool --workdir /etc/prometheus prometheus test rules alerts.test.yml
# Stop only the disposable project; retain volumes/evidence.
acceptance stop
```

Executed: Ruff passed; strict mypy passed (12 source files); **102 pytest tests passed**, including isolated PostgreSQL transaction/fault tests and real Grafana HTTP checks. Compose configuration validated; disposable service images built. Promtool: 11 rules valid, seven scenario tests passed. Deterministic stress checks exact row counts, bounded queue, atomic insert rollback, generation invalidation, stale events, optional sampling and supporting evidence. Public smoke output, builds, security results, raw samples and derived results are under `docs/evidence/phase1-2/`.

Remaining limits: short public observation, no long-duration disk exhaustion exercise, no external alert delivery, no in-place original Grafana upgrade validation, and no fresh comprehensive image vulnerability scan. The original security-review dependency/container risks remain applicable. Full-feed persistence still consumes substantial disk. No live execution, exchange credentials, withdrawal capability, leverage or derivatives were added.
