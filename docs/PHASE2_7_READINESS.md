# Final forward-evaluation readiness hardening

This updates draft PR #6 in place from `e0f71ed390c9cce1116a893dd62910b6755478e8`. Existing acceptance evidence and reports remain unchanged. No pull request was merged and no 14-day evaluation was started. The platform remains public-data/paper-only, without exchange credentials, authenticated orders, withdrawals, leverage, derivatives or paid services.

## Decision boundaries

- **Implementation readiness: CONDITIONAL PASS**, subject to review of the recorded residual risks and assumptions. Recovery, ordered source closure, accounting, explicit diagnostics policy and disk guards have executed regression and fault evidence.
- **Supervised minimum 14-day evaluation readiness: CONDITIONAL PASS**, requiring explicit human approval, the measured capacity preflight, hourly supervision, exact source/configuration pinning and acknowledgement that Coinbase/Crypto.com directions remain unevaluable where fee/rule evidence is UNKNOWN. Do not describe this as a fully fee-verified four-venue trading experiment.
- **Unattended operation readiness: FAIL.** Residual image findings, manual recovery/supervision requirements, incomplete live per-session alert coverage, variable disk growth and missing account-specific assumptions prevent this claim.

The completed final measured acceptance results appear below. A 14-day result cannot be inferred from this short acceptance.

## Exact source, images and changes

The runtime source for final acceptance is `f26bdc0` (full hash in `docs/evidence/readiness/final-runtime-identity.json`). Every installed Python module hash was compared with the workspace before measurement. Container ids, image hashes, start times, mounts, ports and restart counts are retained. Later report/test-harness edits do not alter those installed modules. The final application scan is matched to the measured image id.

Changes on the same feature branch:

1. `ab39681`: additive Alembic 0003, transaction-ordered source windows, complete recovery snapshots/checkpoints, attempt audit, capacity guard, published assumption files and initial container remediation.
2. `16c9791`: explicit derived-diagnostic aggregation and a flattened final PostgreSQL filesystem without the obsolete Go-linked helper.
3. `f26bdc0`: reproduced timer-settlement Decimal context fix and same-source resume requirement.

The original snapshot code sorted books on serialization, changing venue priority after restart; preserving insertion order fixes this. A separate regression proved timer settlement inherited caller Decimal precision, changing balances under precision 6 versus 50; timers now use precision 50 consistently. Both fixes have deterministic tests. Kraken checksum and Coinbase sequence implementations were not changed.

Two earlier observation attempts were deliberately stopped before 900 seconds when final remediation/precision changes were needed. Each supplied approximately 360 seconds of sampled evidence. Their raw samples remain as `pre-remediation-*` and `before-precision-*`; they are not concatenated or counted as completed acceptance. A separate fault-database bootstrap initially raced PostgreSQL initialization, causing a migration failure and 17 undefined-schema test failures. That attempt is retained. Waiting for health, applying the clean migration and rerunning produced **177 passed** with all integrations enabled.

## Recovery architecture and evidence

Snapshots include cash, inventories and cost basis per venue; valuations, fees, realised P&L and risk/peak/drawdown state; reservations; pending and partially settled legs; visible-price consumption; logical time; latest-book decision order; event counters and complete configuration. Financial state and source progress commit in the same transaction with the ordered event ledger. No settlement has an external exchange side effect.

Checkpoints use monotonic versions and deterministic request digests. A repeated acknowledged-or-ack-lost request is idempotent. Conflicting versions/configuration or source identities fail closed. A process that dies before commit loses only its uncommitted memory changes; it reloads the previous state and the same closed input window. A commit that succeeded before an acknowledgement was lost is discovered as the newer durable checkpoint. Legacy sessions without recovery state cannot be silently resumed/reset.

Real attempt start/end metadata is separate from logical paper-event time. SIGKILL leaves an open attempt without an invented terminal time. The inspector reports a recoverable checkpoint and outstanding attempts; a successful resume labels itself. Replayed logical timers may precede restart wall time; their durable checkpoint and attempt timestamps expose this. They are not proof of continuous live observation. Exact financial equivalence requires the same executable source/configuration and immutable inputs, not an unreviewed code upgrade.

Executed faults include checkpoint rollback after event insertion errors, exact retries, conflicting workers, timeout while waiting for source closure, memory loss before/after reservation/buy/settlement, partial-leg restoration and actual subprocess termination. `actual-worker-recovery/results.json` records **SIGKILL at reservation, after the buy leg and after settlement** in a separate disposable fault project. Each resumed worker completed exactly one synthetic trade; its **entire simulator snapshot** equalled uninterrupted replay of its immutable source windows. Synthetic positive-price fixtures test mechanics and are not evidence of market profit.

## Source-data completeness guarantee

Numeric observation ids are no longer treated as commit order. A database BEFORE INSERT trigger takes one advisory transaction lock before allocating `source_sequence`, held until commit/rollback. Window closure takes the same lock, selects a bounded committed source interval and records its closure time, actual row count and SHA256. A later transaction cannot commit an earlier allocated source sequence behind an already closed boundary. Rollback gaps are legitimate sequence holes, not missing observations. Tests explicitly preallocate a lower numeric id and commit it later, prove source order still includes both rows, and verify cancelled/rolled-back writers.

A window survives interruption unchanged. The checkpoint atomically advances over its exact row count and source boundary. Frozen observation updates/deletes and window mutation are blocked; trigger/hash failures stop processing. Initial scope is explicit: new sessions normally begin after the current committed source watermark; `--from-start` includes all protocol-certified rows. Legacy NULL-sequence history is counted as uncertified, not silently backfilled or declared complete.

The guarantee assumes enabled migration triggers, supported append-only writes, and no privileged trigger bypass, TRUNCATE or manual mutation outside the protocol. The reader checks enabled triggers and rejects a regressing closure clock. It does not certify upstream exchange delivery or data never received. Historical reconnect-generation boundaries and exact socket arrival times are still absent from Phase 1 book histories.

Simulation availability is the immutable server closure time, a conservative upper bound. Event and receipt timestamps remain unchanged, including out-of-order receipts. Due actions execute before newer information becomes available; delayed commits can be stale/rejected but are still counted as processed inputs. Bounded windows and one-second polling may add more latency than the configured simulated leg delay. This conservatism is explicit, not an attempt to recreate unobserved live fills.

## Fees, constraints and public-paper scenarios

Official verification date: 2026-11-08. Full dated sources and API constraint responses are in `fee-assumptions.json`, `instrument-rules.json` and the later `cryptocom-fee-verification-addendum.json`. The addendum supersedes the earlier JS-only verification limitation without rewriting that evidence or the executed scenario configurations.

| Venue | Published/configured maker / taker | Verification and limitation |
|---|---|---|
| Binance | 0.100% / 0.100% | Official regular-user spot table; no BNB/VIP/promotional discount assumed |
| Kraken | 0.40% / 0.80% | Official Tier 1 change notice updated 2026-10-30 and current Pro table; do not use an old 0.25%/0.40% ladder |
| Coinbase Advanced | UNKNOWN | Official help requires sign-in for numeric tiers; Coinbase Exchange is a different product and its rates were not substituted |
| Crypto.com Exchange | 0.250% / 0.500% verified after scenario execution | Rendered official Level 1 table, <USD10,000 monthly volume, no CRO balance; tested configs conservatively remain UNKNOWN and incomplete minimum-notional rules still exclude these directions |

Sources: [Binance spot fees](https://www.binance.com/en/fee/trading), [Kraken tier changes](https://support.kraken.com/articles/cross-platform-fee-tier-changes), [Kraken Pro fees](https://www.kraken.com/pro/fees), [Coinbase Advanced fees](https://help.coinbase.com/en/coinbase/trading-and-funding/advanced-trade/advanced-trade-fees), [Crypto.com fees](https://crypto.com/exchange/document/fees-limits).

Binance exchangeInfo supplies quantity steps/min/max, minimum market notional and price ticks. Kraken AssetPairs supplies minimum quantity/cost, lot precision and price tick; its empty fee arrays were not treated as zero. The Kraken scenario maximum of one base unit is a conservative configured research cap, not a verified venue ceiling; maximum trade size also remains USD50. Coinbase public market products supply increments, quantity bounds and minimum quote size, but unknown Advanced fees still block those directions. Crypto.com instruments and the rendered trading-rules table verify native USD identity, ticks and maximum USD notional, but do not establish all required minima; incomplete rules remain absent/unevaluable.

GBP500/1000/1500 files preserve timestamped GBP/USD=1.25, 0.5% conversion and 1% funding assumptions, default adverse slippage/latencies, risk limits and USD5 assumed rebalance cost. These are explicit research inputs, not current account/FX quotations or account eligibility claims. UK public API reachability is separate from permission to trade; no accounts were accessed.

The final three bounded public-forward scenarios are in `verified-paper-scenarios/`. Each observed approximately 8,666 source rows in 60 seconds, initialized a prefunded portfolio and remained paused/recoverable afterward. GBP500/1000 recorded 26,755 positive raw-spread evaluations and 853 fee-adjusted theoretical evaluations; these were **evaluations, not positive profitable opportunities**. Zero capital-feasible reservations, zero simulated legs and zero completed trades occurred. The larger scenario differs by one input at the measurement boundary. Rejections include unknown fees, net threshold, event skew and stale/unsynchronized books. Trading fees and realised inventory-sale P&L are zero because nothing filled. Total marked P&L was approximately -USD9.37/-18.74/-28.11, predominantly explicit conversion/funding/initial-spread costs plus marked inventory movement; this is not realised arbitrage loss or a forecast. Inventory was not depleted by trades and unhedged quantities stayed zero. At least the GBP500 final valuation was incomplete because Binance ETH/USD was stale; marked totals include stale marks and must not be treated as an executable liquidation value. Detailed per-venue balances, valuation flags and all assumptions remain in reports.

## Storage strategies and capacity

The prior 23.22GB/day number was a one-minute extrapolation. Initial preflight found about 658GB free; using that book-only rate, 14 days plus 30% safety and 5GiB headroom required about 428GB. This is not a disk reservation or guarantee against market activity, other workloads, WAL or monitoring growth.

A real three-scenario all-diagnostics run added about **56MB of event-table allocation in 60.87 seconds**, independently of book growth. That would make a book-only budget misleading. The explicit `evaluation_log_mode=aggregate` policy stores raw-spread/theoretical/rejection counts in recoverable state while retaining **every source observation and every financial event**. It is not book sampling. Exact counters and detailed diagnostics remain reproducible from frozen inputs/configuration.

A controlled comparison over the **same 8,666 immutable public inputs** produced 86,524 full diagnostic records / 13,348,320 canonical bytes versus one 240-byte allocation event under aggregation. Financial state, every counter and financial-event content were identical. There were no market trades in this sample; positive/partial/failed financial-event equivalence is covered by deterministic tests. This is a derived-journal reduction, not a claim that total database usage shrank or observations were compressed. Existing full diagnostic records were retained. Scoped aggregate sessions stored one allocation record each (254/257/257 JSON bytes); page-allocation deltas can be zero despite real writes.

| Strategy | Evidence and recovery | Capacity/operation implication |
|---|---|---|
| Full source history once + aggregate derived diagnostics | Complete committed inputs/windows and financial ledger; diagnostics recomputable | Recommended lowest-cost implemented credible mode; forecast actual total growth and retain headroom |
| Full source + every repeated diagnostic | Same input quality, far more redundant derived rows | Measured additional high volume; do not budget as book-only |
| Explicit book sampling | Lower-volume degraded source history | Not lossless; cannot certify omitted frames or claim full-depth replay completeness |
| Verified closed-window archives | Lossless source order, clocks, closure and hashes | Useful independent evidence/backup; exporting alone does not free PostgreSQL data |
| Automatic expiry/delete | Not implemented | Prohibited here; no automatic original-data deletion or volume removal |

Duration-based preflight and configurable headroom operate on the actual read-only mounted PostgreSQL filesystem. Critical disk pressure or missing filesystem telemetry stops new collection; a dedicated Prometheus alert fires. Startup preflight prevents restart from authorizing collection below headroom. A supervisor must also pause paper workers and review capacity, not blindly restart or delete data. Retention remains a read-only eligibility count. Window exports verify hash/schema/count/source order and refuse overwrite. No archival workflow automatically deletes protected source data. If measured projected total growth no longer fits, full-history 14-day collection is **not ready** until capacity is approved or a separately reviewed lossless archival/reclamation plan exists.

## Security remediation and residual risks

Pinned application base plus available Debian upgrades reduced HIGH findings from 49 to **47**, with **zero CRITICAL** and zero locked Python package matches. PostgreSQL Alpine packages were upgraded, the obsolete Go-linked privilege helper replaced with C su-exec, and the final patched filesystem flattened to exclude the removed binary/lower-layer inventory. PostgreSQL 16.15 startup, privilege drop, existing disposable-volume compatibility, clean migration and recovery faults passed; the final actual image scans **0 HIGH / 0 CRITICAL**, versus prior 28/1. Intermediate image inventories still flagged the removed Go helper; those reports and the final filesystem result are all retained, without scanner suppressions.

Current official Grafana 13.3.0, Prometheus 3.16.0 and Alertmanager 0.34.2 were checked against upstream releases. Their compatible latest official images still report **54/2/2 HIGH**, zero CRITICAL. A custom upstream Go/plugin dependency rebuild was not validated. No advisory was ignored to create a clean result. `security-finding-triage.json` covers each remaining package match: installed/fixed version, advisory/conditions, exposure, remediation path and proposed residual-risk decision. Many Debian findings have no fixed version in the selected stable distro; other upstream module fixes require rebuilt official binaries. Package presence is not proof of reachability or exploitability.

Loopback bindings, anonymous Grafana Viewer, blocked admin/basic login, non-root application and internal plain-HTTP scrape/datasource paths reduce exposure. They do not make vulnerable libraries harmless. No credentials or private exchange paths exist. Residual risk is proposed only for isolated supervised research; human approval is still required for prolonged operation. CI's ephemeral upstream PostgreSQL service is test infrastructure, not the deployed patched Compose image.

## Regression and operational runbook

Executed final checks: **177 pytest tests passed**, including isolated PostgreSQL/Grafana, concurrency, actual process faults, archive corruption, Decimal precision and replay determinism; Ruff passed; strict mypy passed for 24 source files; clean Alembic migration/schema check; Compose builds/configuration; Promtool config/rule tests including critical disk stop; Grafana Viewer/admin restrictions. Detailed command/evidence files accompany this report. Earlier failed development/bootstrap attempts are retained separately, not presented as passing.

1. Freeze source/image hashes and scenario config, apply migration only to a reviewed dedicated database and confirm all source triggers. Large existing replay/source indexes may need a maintenance window; the original running database was not migrated here.
2. Run capacity preflight for the exact planned duration using measured **total** growth, safety margin and shared Docker/host usage. Record free bytes and memory/CPU headroom. Do not count reclaimable old data as free without approved action.
3. Start a uniquely named project with unused loopback ports/volumes. Verify all eight market identities, source ordering, Prometheus scraping/alerts and Grafana access. Sparse Binance native USD books may legitimately be stale; do not fill using them.
4. Run a bounded scenario first, inspect `session.json`, current durable checkpoint and source progress. For restart, keep the same source/config and invoke `--resume SESSION_ID` into a new attempt directory. Inspect outstanding attempts; never label a recovery gap uninterrupted.
5. During approved supervised operation, inspect checkpoint/cursor progress, failed/incomplete windows, fee/rule exclusions, pending/unhedged legs, stale marks and local alerts hourly and after every outage. Result dashboards are not a full live per-session heartbeat system.
6. On critical capacity, pause paper workers and collection, preserve evidence and verify closed exports. No automatic deletion. Re-run capacity preflight before restart. On state/source hash/version conflicts, fail closed and perform forensic review.
7. Stop only disposable/approved project services. Retain volumes, windows, checkpoints, attempt logs and manifests. Do not merge PR #6 or start 14 days as part of this acceptance.

Minimum evaluation configuration: four named exchanges, BTC/USD and ETH/USD; full book persistence (`CROSSX_BOOK_SAMPLE_INTERVAL_SECONDS=0`); certified source windows; same-source recoverable paper sessions; explicit `evaluation_log_mode=aggregate`; GBP500/1000/1500 separate capital pools; verified fees/rules or UNKNOWN exclusions; configured critical/headroom thresholds; reviewed total daily budget and `CROSSX_CAPACITY_RUN_SECONDS=1209600` preflight; no live execution. See `FORWARD_EVALUATION.md` and the exact commands/evidence below. No 14-day process has been launched.


## Completed final 900-second acceptance

Measured **2026-11-08 10:56:34.566050–11:11:34.574617 UTC**, **900.0085 seconds**, 61 flushed samples at nominal 15-second intervals. Five-second Prometheus history has 181 samples per pair. Every service kept the same container id/start time and zero restart count throughout. No measurement errors occurred; the longest sampler collection took 2.562 seconds. Four exchanges/eight native USD books remained synchronized at sampled checks, with zero reconnect/integrity counter increments. Synchronization is sequence/book integrity, not freshness. No missing pair coverage; freshness exclusions below are material.

| Exchange / symbol | Persisted counter delta | Rows/s | Max sampled event age s | Max sampled receipt age s | Event age >5s samples /181 |
|---|---:|---:|---:|---:|---:|
| binance BTC/USD | 871 | 0.968 | 18.795 | 18.669 | 140 |
| binance ETH/USD | 353 | 0.392 | 37.923 | 37.801 | 157 |
| coinbase BTC/USD | 12,460 | 13.844 | 4.772 | 4.717 | 0 |
| coinbase ETH/USD | 11,377 | 12.641 | 4.634 | 4.579 | 0 |
| cryptocom BTC/USD | 5,427 | 6.030 | 6.962 | 6.816 | 16 |
| cryptocom ETH/USD | 4,297 | 4.774 | 6.021 | 5.888 | 11 |
| kraken BTC/USD | 71,285 | 79.205 | 5.981 | 5.965 | 13 |
| kraken ETH/USD | 36,688 | 40.764 | 4.929 | 4.916 | 0 |

Counter snapshots and SQL timestamp-bounded counts differ by a few rows because each sampler component is concurrent rather than one database/metrics transaction. Both raw methods are retained. The five-second Prometheus ages include scrape/sample lag (up to roughly one scrape interval); stale sample fractions are conservative monitoring indicators, not exact continuously measured stale durations. No outage duration is inferred by multiplying endpoint samples as if they were continuous observations.

Binance BTC/USD delivered 871 observations: low activity is not a checksum defect. Its received frames had event-to-receive mean **123ms**, maximum **698ms**, and zero observations already >5 seconds old on receipt. ETH/USD mean was 121ms; neither Binance pair had sequence errors or reconnects. Long intervals between fresh frames explain the stale latest-book ages. Market inactivity is consistent with this evidence; proving exchange-side inactivity rather than upstream batching would require independent venue evidence. The simulator's original-clock freshness/skew rules exclude stale books. SQL diagnostics across all eight pairs found zero event-ahead-of-receipt and zero >5-second event-to-receipt observations; this short interval does not disprove historical delays. Persisted depth stayed 25 bids/25 asks per observation.

The database grew **158.78MB**, including **158.88MB** allocated book relation growth (other relations can shrink/reuse pages), over 142,758 rows: **158.62 rows/s**, sampled interval range 73.32–337.91 rows/s. WAL generation grew 189.45MB; this is cumulative generated WAL, not retained filesystem growth. Mean persistence call latency was **5.359ms**, histogram p95 <=25ms/p99 <=50ms. Five-second queue residence counter deltas give **3.721ms mean**. Fifteen-second queue samples peaked at 33; five-second Prometheus captured **one 128/128 sample at 10:58:24 UTC**. This brief bounded saturation was not hidden or solved by increasing capacity. It occurred during additional paper/fault/diagnostic work on the shared local Docker host; the samples do not isolate a unique cause. Advisory-lock closure and DB/host contention remain throughput risks. No loss or generation/integrity failure was observed, but finer latency instrumentation/load isolation is needed before asserting spike-free operation.

Application CPU averaged 21.73% (max 37.29%) and memory 84.2–92.8MB. PostgreSQL CPU averaged 7.36% (max 34.48%), max memory 166.2MB; Grafana max 331.6MB, Prometheus 55.2MB, storage exporter 49.8MB. Docker CPU percentages use one CPU as 100%; these are sampled host-dependent measurements, not limits or a leak-free guarantee.

This interval extrapolates to **15.25GB/day book allocation**, **213.54GB/14 days** before safety/headroom/other components; 30%+5GiB gives approximately **282.98GB**. The prior 23.22GB/day assumption remains the more conservative book-only budget (~428GB with margin). WAL generation extrapolates 18.19GB/day but is recycled subject to configuration; do not add that cumulative value blindly to retained disk. Neither short interval proves a stable daily rate. Three aggregate paper sessions over 60.50s added about 0.566MB session and 0.0655MB window allocation while collection ran; a linear 14-day extrapolation of that brief allocation is only a sensitivity (~11.3GB/three sessions), not a measured retention guarantee. Monitoring, checkpoints, indexes, archives, shared workloads and all six proposed scenarios require a fresh total-growth budget. No data was deleted.

Prometheus targets were up, all 14 configured alert rules loaded, Grafana datasource health wasOK and 33 provisioned panels were present. Direct checks returned anonymous dashboard200, anonymous admin403, admin/admin403. These checks do not prove absence of every application vulnerability. The original stack's baseline ids/images/start times/restart counts/network/volume/port metadata match after normalizing volume-name representation (`original-preservation-comparison.json`). It was never stopped, recreated or migrated.

Final repeated validation with all three integration environment variables set: **177 passed in 2.36s**. A preceding validation accidentally omitted the dedicated fault-test variable and recorded176 passed / 1 skipped; that output remains separate. Ruff passed and strict mypy passed 24 source files. A real closed-window dry run, export and independent verification succeeded without modifying source rows (`window-export-*.json`). Exporting has not reclaimed disk. Exact commands follow; use fresh evidence paths and ports for each repetition.


## Evidence and human acceptance commands

[commands.sh](evidence/readiness/commands.sh) records the exact project names, ports, environment variables, test and observation commands. Do not run it wholesale: select a fresh isolated name/unused ports and follow health/migration checks before each dependent command. `--help` is read-only. Recovery subprocess tests were invoked with:

```sh
.venv/bin/python ops/check_forward_recovery.py --database-url postgresql+asyncpg://crossx:crossx@127.0.0.1:18433/crossx --out docs/evidence/readiness/actual-worker-recovery --source-commit f26bdc083cc331c5ef586fbac259ce6a598d613d
.venv/bin/python ops/run_readiness_paper.py --database-url postgresql+asyncpg://crossx:crossx@127.0.0.1:18432/crossx --out docs/evidence/readiness/verified-paper-scenarios --source-commit f26bdc083cc331c5ef586fbac259ce6a598d613d --duration 60
```

Both helpers refuse an existing output directory. Use NEW paths for repetition. The first archive check selected a valid empty initial window; the second selected version 2 and verified 162 source observations with matching SHA256. Both artifacts remain. After measurement, only the disposable projects were stopped, retaining volumes. The disposable PostgreSQL service alone was briefly restarted to read/export that nonempty window, then stopped; this is outside the timed 900 seconds and no collector was restarted. Original-stack preservation was independently checked after shutdown. An early metadata comparator returned false solely because it compared volume names with expanded mount objects; normalized and original helper comparisons both confirm unchanged identities.

Large raw evidence files are committed as byte-for-byte gzip copies with original/compressed hashes in `archive-manifest.json`; local originals remain. Verify committed evidence with `cd docs/evidence/readiness && shasum -a256 -c SHA256SUMS`. Decompress a copy into a new path for JSONL replay; never replace earlier evidence. Historical implementation evidence directories were not modified. Raw Trivy outputs, failed development attempts, actual process kills, source/image identities, all measurements and final validations are retained separately.


Shutdown limitation: the ingestion application and both disposable PostgreSQL containers exited 0 after the scoped stop. The storage-metrics sidecar exited 137 at the Compose stop deadline; its current infinite exporter loop has no explicit SIGTERM shutdown handler. This is a confirmed operational gap, not an unexpected restart during acceptance. It does not write/delete observations, but its final scrape/DB connection cleanup is not graceful. Preserved stop/state evidence supports this remaining issue; no claim that every component shut down gracefully is made. It should be addressed and revalidated before unattended operation.
