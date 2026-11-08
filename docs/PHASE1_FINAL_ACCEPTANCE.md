# Phase 1 final operational acceptance

**Verdict: CONDITIONAL PASS for supervised local public-data research.** The hardened source completed a real continuous **900.004507-second** observation of BTC/USD and ETH/USD on Kraken and Coinbase, followed by bounded graceful stop/restart checks. Sustained measurement is complete. Conditions remaining before longer or unattended collection are an agreed storage/retention budget, monitored alert response, and review of inherited image/dependency risks. No production-readiness or profitability claim is made.

## Source, isolation and method

Required and tested source commit: `e7b585320bd80be1ba476c1ce9ff9fb299191291`; branch `codex/phase-1-final-acceptance` starts exactly there and targets `codex/phase-1-2-hardening`. The checkout was clean before evidence creation. Runtime SHA-256 comparison of every Python source file under `/app/src` matched Git at that commit ([source hashes](evidence/final-acceptance/runtime-source-hashes.json)). The installed package actually imported by Python also matched every source hash ([installed identity](evidence/final-acceptance/installed-source-hashes.json)). No application, adapter, database or monitoring implementation was changed. The PR adds a read-only analysis helper and evidence/report only.

| Service | Exact Docker image ID |
|---|---|
| storage-metrics | `sha256:7292c6e0ebd1acde403d41d6fb3a3aa69f3b066a6506fe9c3c36e55b49115e15` |
| app | `sha256:c1b851493277eba54b0cf8d2654387d1f782f93857f7561d91cbae892bff53d2` |
| postgres | `sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368` |
| grafana | `sha256:2f56a900c09165cd9a85d63f59452d3e19189518b2168150a3f4ee1b24da5345` |
| prometheus | `sha256:91fa0183c21d96762de93cb1c0d7818c97846df8815f3985e11b9b3338ff03bb` |

Complete image inspection and Docker runtime versions are in [images.json](evidence/final-acceptance/images.json), `docker-version.json`, and `docker-info.json`. Image tags remain mutable; IDs above identify this test.

Disposable project: `crossx-acceptance-final-20261108t0945`; loopback ports PostgreSQL **17432**, app **18082**, Prometheus **19093**, Grafana **13082**. Preflight confirmed no existing project containers, networks or volumes; Compose contains no external/shared resources or host mounts. Five new service containers, one project network and three new volumes were created. App and storage exporter share only the new project database volume (exporter mount read-only). Existing original and hardening resources were preserved.

[Before](evidence/final-acceptance/isolation-before.json) and [after](evidence/final-acceptance/isolation-after.json) snapshots prove unchanged original container IDs, image IDs, start times, restart counts, volume/network memberships and port bindings. Original research tables were never queried or modified. A distinct fresh `crossx_final_unit` database hosted destructive/fault integration tests; the measured `crossx` database received public books only. Services share the Docker VM and host contention; this is not a capacity benchmark. Concurrent regression tests and smoke collection add host load during the early window.

The existing sampler used monotonic elapsed time, flushed every 15 seconds, and collected SQL counts, raw app metrics, Docker identities and Docker stats concurrently. Prometheus scraped every five seconds; the committed range query uses a five-second grid (lookback can repeat a prior scrape). Monitoring health/provisioning checks ran every fourth sampler point. Warm-up rows are excluded from timed deltas. No deliberate outage or restart occurred inside the measured window.

## Completed continuous measurement

UTC start **2026-11-08T08:52:41.053575+00:00**, end **2026-11-08T09:07:41.054210+00:00** (also Europe/London on this date). **61 samples**, zero sampler errors; largest sample gap **15.007s**, largest collection cost **2.485s**. Initial warm-up count **2194**; timed addition **90665**, mean **100.74 books/s**. Interval rates **39.79–370.45 books/s**.

| Exchange / symbol | New persisted books | Mean books/s |
|---|---|---|
| coinbase BTC/USD | 11450 | 12.72 |
| coinbase ETH/USD | 10828 | 12.03 |
| kraken BTC/USD | 42505 | 47.23 |
| kraken ETH/USD | 25882 | 28.76 |

Readiness and all four synchronization gauges were 1 at every direct sample; all monitored Prometheus targets stayed up. Per-venue reconnect and integrity counter deltas were zero. All five service identities/start times were constant and restart counts remained zero throughout. Unknown maker/taker fees were explicit nulls; startup warnings were present and opportunity rows remained **0–0** while collection continued. Default full history sampling was zero, batch bound 32 and queue capacity 128.

15-second queue peak: **3**; five-second Prometheus-grid peak: **128**, full at **1/181** points. Persistence failure deltas: **0**; shutdown-timeout delta: **0**. All rejection reasons and labels are preserved in `analysis.json.gz`; aggregate rejections **0**.

| Measurement | Mean | p95 / p99 histogram upper bound |
|---|---|---|
| Queue admission | 0.0068 ms | 1 / 1 ms |
| Queue residence | 3.2599 ms | 50 / 50 ms |
| Transaction request | 4.0540 ms | 10 / 25 ms |
| Per-book persistence | 5.2475 ms | 25 / 50 ms |
| Batch size | 2.6183 books | 16 / 32 books |

Means use histogram sum/count deltas, with whole-transaction latency kept separate from per-book commit latency. No division of batch commit latency by batch size is used. Histogram quantiles are bucket upper bounds, not exact percentiles. Batch metrics and SQL reads occur at different times, so small cross-counter discrepancies are expected.

| Exchange / symbol | SQL rows | Event→receive mean / p95 / max ms | Already >5s / future |
|---|---|---|---|
| coinbase BTC/USD | 11450 | 44.712 / 51.536 / 161.853 | 0 / 0 |
| coinbase ETH/USD | 10826 | 45.418 / 50.991 / 158.747 | 0 / 0 |
| kraken BTC/USD | 42506 | 26.701 / 97.716 / 301.427 | 0 / 0 |
| kraken ETH/USD | 25881 | 21.944 / 70.114 / 412.606 | 0 / 0 |

SQL diagnostics cover receipt timestamps in the measured interval and include complete depth checks; all records have 25 bids/asks. SQL receipt-window counts differ from persisted-snapshot deltas because receive and commit boundaries differ. Event age includes exchange clock skew, upstream processing, buffering and scheduling; it is not isolated one-way transport latency.

| Histogram | Labels | Mean ms | p95 upper bound ms |
|---|---|---|---|
| event_age_at_receive_seconds | {"exchange": "kraken", "symbol": "ETH/USD"} | 21.945 | 100 |
| event_age_at_receive_seconds | {"exchange": "kraken", "symbol": "BTC/USD"} | 26.700 | 100 |
| event_age_at_receive_seconds | {"exchange": "coinbase", "symbol": "BTC/USD"} | 44.712 | 100 |
| event_age_at_receive_seconds | {"exchange": "coinbase", "symbol": "ETH/USD"} | 45.418 | 100 |
| event_age_at_evaluation_seconds | {"exchange": "kraken", "symbol": "ETH/USD"} | 29.296 | 100 |
| event_age_at_evaluation_seconds | {"exchange": "kraken", "symbol": "BTC/USD"} | 37.936 | 500 |
| event_age_at_evaluation_seconds | {"exchange": "coinbase", "symbol": "BTC/USD"} | 56.869 | 100 |
| event_age_at_evaluation_seconds | {"exchange": "coinbase", "symbol": "ETH/USD"} | 54.109 | 100 |
| producer_delay_seconds | {"exchange": "kraken"} | 0.155 | 1 |
| producer_delay_seconds | {"exchange": "coinbase"} | 4.843 | 10 |
| reconstruction_seconds | {"exchange": "kraken"} | 0.141 | 1 |
| reconstruction_seconds | {"exchange": "coinbase"} | 4.633 | 10 |

Zero stale rejection during this public window verifies the measured absence of that condition, not a live fault injection. Deterministic tests in the executed full suite exercise stale-event/receive rejection and post-await generation invalidation. Readiness alone does not guarantee book freshness or lossless WebSocket delivery.

| Service | CPU mean / sampled peak % | Memory mean / sampled peak MiB |
|---|---|---|
| storage-metrics-1 | 0.02 / 0.12 | 45.54 / 45.69 |
| app-1 | 18.39 / 29.53 | 76.90 / 78.53 |
| postgres-1 | 3.18 / 10.24 | 92.65 / 134.70 |
| grafana-1 | 2.05 / 17.86 | 301.81 / 415.30 |
| prometheus-1 | 0.21 / 1.34 | 45.29 / 55.91 |

CPU and memory peaks are sampled Docker figures, not guaranteed maxima, CPU profiles or leak proofs. Histograms observe instrumented operations, whereas brief gauge/resource spikes between samples may be missed.

## PostgreSQL storage and budget

| Measurement | Initial bytes | Final bytes | Growth bytes |
|---|---|---|---|
| book_bytes | 2457600 | 99975168 | 97517568 |
| database_bytes | 10271767 | 107797527 | 97525760 |
| wal_bytes | 12191128 | 126054210 | 113863082 |

Committed-transaction statistic delta **35309** includes sampler/exporter queries and is asynchronously updated, not an exact commit ledger. Book storage is table/index/TOAST allocation; measured ratio **1075.6 bytes/book**. Linear book growth extrapolates to **9.36 GB/day (8.72 GiB/day)**; generated WAL **10.93 GB/day**. WAL generation is not retained WAL disk usage. These are 15-minute extrapolations, not completed daily measurements.

Both scrape targets stayed up. Storage exporter success was 1 at 180/181 query-grid points; the initial lookback point carried startup defaults (`storage_up=0`, last-success=0, free-space=0), followed by successful polls. This initial zero is an uninitialized gauge, not measured disk exhaustion. Lowest free space at successful-poll points was **617.26 GiB**. This is shared Docker filesystem capacity, not reserved capacity or a per-volume quota. Defaults remain 30 GiB dataset warning budget, 20 GiB/day book-growth budget and 5 GiB free-space warning. At this window's growth, 30 GiB holds approximately **3.44 days** from empty before headroom and other data. No automatic retention, deletion or disk-exhaustion guarantee exists. Operator capacity approval, export/replay requirements and alert response remain prerequisites for prolonged operation. No retention/deletion experiment was run against research data.

## Monitoring, security and lifecycle

Both `crossx` and `crossx-storage` targets were healthy; all **11** runtime alert rules loaded with health `ok`. Promtool validated configuration/11 rules and passed the seven configured firing scenarios. Grafana datasource health was OK; **25 panels** were provisioned in every monitoring sample. Anonymous dashboard HTTP 200 with `canSave:false` and `canAdmin:false`; anonymous and `admin/admin` admin-settings APIs both HTTP **403** ([security probe](evidence/final-acceptance/security.json)). This checks a fresh disposable Grafana, not an in-place upgrade of the original stack. No external alert delivery/Alertmanager is configured; local rule evaluation requires an operator to monitor it. Runtime alert/target data and complete time series are retained in `analysis.json.gz`.

Only after the complete 900-second end marker, two cooperative stops exited 0 within Docker's 15-second bound: **0.864s** and **0.741s**. The stopped database was stable for three seconds; restart reached synchronized collection and all four pair counts increased during the subsequent 30-second check. Opportunities stayed zero. Logs verified two research starts, four unknown-fee warnings and no drain-timeout event. See [lifecycle.json](evidence/final-acceptance/lifecycle.json). These are graceful lifecycle checks, not crash/power-loss durability tests.

Only the disposable project's five services were stopped. Its containers, network and **all three volumes remain retained**, alongside every existing volume and earlier evidence. The original four services remain unchanged and running.

## Before/after evidence and caveats

| Measurement | Previous acceptance | Hardening public run | Final acceptance |
|---|---|---|---|
| Duration seconds | 900.005 | 180.004 | 900.005 |
| Persisted books | 143237 | 30769 | 90665 |
| Mean books/s | 159.15 | 170.93 | 100.74 |
| 15-second sampled queue peak | 128.0 | 5.0 | 3.0 |
| Books GB/day extrapolated | 14.87 | 16.16 | 9.36 |
| Event >5s on receive | 381 | 0 | 0 |
| Default admin API | 200 | 403 | 403 |

Previous full acceptance used `fae2e6c`; hardening evidence ran 180 seconds and preceded the final optional-sampling generation guard. This run tests exact final `e7b5853`, including that guard's deterministic regression (sampling disabled in public collection). Public market rates, clock periods and shared host load differ, so throughput/storage changes are observational comparisons, not controlled improvement ratios. The earlier controlled synthetic batch comparison remains separate evidence; it was not rerun here. Prior acceptance recorded a five-second-grid queue peak of 128 and 19/181 full points, 381 stale-on-receive records and default admin HTTP 200; this run's exact sampled/SQL/security measurements above address those observed regressions without proving maximum capacity or future freshness.

## Executed checks and remaining limits

- Ruff: passed, including new analysis helper; strict mypy: passed, 12 source files.
- Full pytest: **102 passed**, zero skips, including isolated PostgreSQL/fault tests and real Grafana security integration. Initial attempt: **98 passed, 4 failed** because the fresh test database had not been migrated; `alembic upgrade head` on that new database corrected setup, then all 102 passed. Both outputs retained; no application fix was required.
- Standalone acceptance Compose rendered and images built; primary Compose validation passed. Promtool config/rules/scenarios passed.
- Separate real public adapter smoke: 35 seconds per venue, BTC/USD and ETH/USD, zero reconnects/integrity errors; both passed.
- Full continuous acceptance, SQL diagnostics, monitoring/security, source identity, original preservation and bounded lifecycle checks completed.

No new application correctness or security defect was reproduced in this measured run. A transient full-queue point remained visible at 08:59:01 UTC (1/181 five-second-grid points), despite the 15-second sampler peak of 3; batching has not eliminated every short pressure burst. The storage warm-up gate checked scrape health rather than a successful exporter poll: require `crossx_storage_up=1` and a recent nonzero last-success clock before future measurement windows. This harness limitation is retained explicitly rather than discarding the initial raw point. Remaining risks include substantial unbounded history, mutable base tags, inherited dependency/container vulnerability findings (no new comprehensive vulnerability scan), no notification routing, no dedicated resource limits/capacity reservation, finite sample resolution and a 15-minute duration. This is not a day-long soak, lossless-ingestion proof, live execution acceptance or positive-fee profitability test. No exchange credentials, order submission, balances, leverage, derivatives, withdrawals or paid infrastructure were used.

## Human acceptance and evidence integrity

Review the report, exact source/image hashes, raw samples and failed-then-corrected setup logs before accepting. [commands.sh](evidence/final-acceptance/commands.sh) records this run's commands; preparation, warm-up, identity, security and post-window scripts are preserved as text. Archives retain raw bytes with SHA-256 hashes in `manifest.json`. Existing evidence was preserved.

For a **new** run, choose unused ports and a never-used project name; do not reuse this retained dataset or invoke bare default-stack lifecycle commands:

```sh
export FINAL_PROJECT=crossx-acceptance-final-REPLACE_WITH_UNIQUE_UTC
export ACCEPTANCE_DB_PORT=17433 ACCEPTANCE_APP_PORT=18083
export ACCEPTANCE_PROM_PORT=19094 ACCEPTANCE_GRAFANA_PORT=13083
git rev-parse HEAD  # verify e7b585320bd80be1ba476c1ce9ff9fb299191291 source
# Inspect docker ps, compose ls, volume/network names and port availability first.
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml config --quiet
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml up -d --build
# Wait for four book counters, readiness/sync, both scrape targets and Grafana health.
.venv/bin/python ops/observe_acceptance.py --project "$FINAL_PROJECT" \
  --duration 900 --interval 15 --app-port 18083 --prom-port 19094 \
  --grafana-port 13083 --out /tmp/NEW-final-observations.jsonl
.venv/bin/python -m ops.analyze_final_acceptance /tmp/NEW-final-observations.jsonl \
  --port 19094 --out /tmp/NEW-final-analysis.json
.venv/bin/python -m ops.capture_acceptance_database /tmp/NEW-final-observations.jsonl \
  --project "$FINAL_PROJECT" --out /tmp/NEW-final-database.json
# Capture a fresh original/acceptance identity snapshot before lifecycle checks.
.venv/bin/python -m ops.check_acceptance_lifecycle --project "$FINAL_PROJECT" \
  --app-port 18083 --prom-port 19094 --grafana-port 13083 \
  --original-snapshot /tmp/NEW-isolation-before.json --out /tmp/NEW-lifecycle.json
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml stop
# Retain volumes; no down --volumes, pruning, deletion or automatic merge.
```

Human acceptance remains a review decision. The draft PR targets the hardening branch and must not be merged automatically.
