# CrossX Phases 2–7 implementation

**Verdict: CONDITIONAL PASS for supervised research implementation review. Not accepted for unattended 14-day operation.** Four native-USD public adapters, capital-aware paper execution, deterministic replay, persistence and local monitoring are implemented with executed regression coverage. Short probes do not establish sustained reliability, realistic execution quality, UK trading eligibility or profitability. The minimum 14-day evaluation has not started. Nothing in this task merges a pull request or changes the original running stack.

## Baseline and exact implementation

Verified clean `main` at the requested `74b08e7cbb9eb77c1a35b25bce5202e656f3f252`. One feature branch: `codex/phases-2-7-paper-research`; one draft PR targets main. Existing Phase 1 acceptance evidence is unchanged. Original seven-phase numbering is retained in the mapping below. The baseline repository documents Phase 1 but does not contain a separate detailed original seven-phase roadmap; the descriptive labels map the capabilities in this request rather than asserting recovered historical phase titles.

| Phase | Implementation | Main evidence |
|---|---|---|
| 1 | Existing ingestion/hardening/acceptance retained | Existing tests/evidence preserved |
| 2 | Four-exchange public spot integration, actual quote identity | New adapter tests; official market responses; public smoke |
| 3 | Generalized directional detection; paper evaluations with fees, depth and constraints | Four-venue/quote/fee tests; raw versus theoretical/rejected events |
| 4 | GBP funding, equal/selective allocation, venue cash/base, reservations and risk gates | Three capital values; conservation, depletion and risk tests |
| 5 | Sequential uncertain paper legs, partial/failure outcomes, settlement and P&L | Fill, second-leg failure, unhedged exposure and venue reconciliation tests |
| 6 | Receipt-ordered database/file replay, deterministic reports, opt-in bounded forward mode | Byte-identical repeated scenario; read-only DB replay; forward smoke |
| 7 | Paper result metrics/dashboard, local notifications, safe archive, security review and evaluation protocol | Promtool, actual webhook delivery, Grafana HTTP checks, Trivy, archive verification |

Implementation commits:

- `c6273ede4dbdcff1a0f26df87c1cec139d96da5d`: adapters, paper ledger, replay/forward, additive schema and operations.
- `0b786cf`: forward pool cleanup on initialization failure, with regression.
- `0a28f827bf6e029e1f19724cd2fdb402e936c864`: per-venue P&L reconciliation and conservative rebalancing thresholds.
- `ccbe810`: upgrade and pin the newly added optional Alertmanager after scanning its older candidate.

Later evidence/documentation commits do not change these Python implementation identities. `timed-image-identity.json` verifies every installed Python source hash in the measured ingestion image against c6273ed. `final-image-identity.json` verifies installed final source against 0a28f82; subsequent optional-image/configuration and documentation changes do not change Python code. Image IDs are recorded, not inferred from a mutable tag.

## Architecture, markets and assumptions

`adapters/__init__.py` now owns a four-venue registry. The existing consumer still uses a bounded queue of 128, immediate transactions up to 32 books, generation-aware invalidation, event/receive freshness, persistence deadlines and controlled shutdown. Kraken CRC32 and Coinbase connection-sequence algorithms are unchanged. New public adapters inherit transport limits/reset behavior; `BinanceAdapter` bootstraps REST snapshots after subscribing and checks diff ranges and covered depth; `CryptoComAdapter` validates snapshot/delta chains and responds to public heartbeats. No exchange account, authenticated request, live order or withdrawal API was added.

The official instrument responses on 2026-11-08 listed native USD spot markets on all four venues:

| Venue | BTC | ETH | Quote asset |
|---|---|---|---|
| Kraken | BTC/USD | ETH/USD | USD |
| Coinbase | BTC-USD | ETH-USD | USD |
| Binance | BTCUSD | ETHUSD | USD |
| Crypto.com | BTC_USD | ETH_USD | USD |

`docs/EXCHANGES.md` links current official protocol and instrument references and UK-context notices. `markets.json` preserves filtered endpoint responses and timestamps, including derivative metadata solely to demonstrate spot filtering. Only the spot allowlist can enter the adapters. USDT/USDC remain separate canonical assets. USD detection and simulation reject unsupported cross-quote conversion. No stablecoin parity or synthetic USD-equivalent price is fabricated. Public-data access is not permission to trade; no eligibility check or account access was attempted.

For four compatible venues there are 12 directional comparisons per symbol and 24 across BTC/ETH; positive opportunities are a subset. Tests verify six positive directions in an ordered four-price fixture and reject unknown fees and quote mismatch. Repeated raw research evaluations are not completed trades. The paper state machine prevents overlapping same-symbol actions, reserves shared capital and tracks consumed liquidity at visible prices so identical/repeated updates cannot refill it.

`PaperConfig` contains explicit GBP/USD and assumption timestamp, conversion/funding costs, venue fees and order rules, allocation, quantities, exposure/inventory/trade/loss limits, expected return, slippage and sequential latency. All financial arithmetic stays Decimal with precision 50 in ingestion/settlement. Default example fees and rules are unknown: fill them with dated research assumptions before evaluating execution. Maker/taker schedules remain configurable; paper fills use taker rates on both legs. No account fee tier is invented.

Default funding assumptions are illustrative: GBP/USD 1.25 in the example, 0.5% conversion cost and 1% funding cost; 50% funded cash, 50% initial base endowment split BTC/ETH. For GBP 500/1000/1500, equal allocation leaves approximately USD 76.96/153.91/230.87 cash per venue and USD 38.48/76.96/115.44 initial cost basis in each venue's BTC and ETH. Selective allocation across two venues doubles these per-active-venue budgets without increasing aggregate capital. These budgets can fall below required quantity/notional and fee thresholds. They are not current exchange cost quotations or recommendations.

See `PAPER_RESEARCH.md` for accounting detail: raw spread → theoretical net after fees/slippage/assumed rebalancing → capital reservation → executable simulated leg outcomes → completed or unhedged trade. Buy and sell legs occur at different simulated times; delayed/failing/partial fills need not realize a positive signal. Reservations settle at their own venues; inventory never transfers instantly. Realised inventory-sale P&L, unrealised NAV change, paired matched cashflow and total GBP-valued P&L are distinct. Stale valuation marks are listed; unavailable initialization yields null financial values, not zero opportunity or invented losses. Per-venue results reconcile aggregate balances and realised P&L. No liabilities or borrowing exist.

## Database, replay and storage safety

Alembic 0002 only adds `paper_sessions`, `paper_events` and a receipt/id replay index. Config, input/source provenance, allocation/reservation/fill/rejection events and final balances/valuation are JSONB with Decimal strings. Existing book/opportunity rows remain intact. A legacy-row migration test proved exact payload preservation across 0001→0002; Alembic check reports schema parity. Destructive downgrade is blocked with an explicit archive/manual-review error. Building the receipt index can require time and locks on large data; schedule an approved migration window before upgrading the original database. It was not migrated by this task.

Replay uses a repeatable-read read-only server cursor, ordered by receipt timestamp and id. Exchange event timestamps remain unchanged; regressing event clocks are rejected. Due simulated legs execute before newly encountered information, eliminating look-ahead from future books. Bounded event batches commit transactionally; duplicate event keys prove rollback of the whole checkpoint and result update. New output directories are mandatory. A failed session remains incomplete; automatic portfolio resume is not implemented.

Six synthetic scenarios cover GBP 500/1000/1500 and equal/selective allocation under explicit **synthetic**, not real-market, prices/fees/rules. A repeated GBP1000/equal run produced identical event and report bytes. `final-scenarios/` records configs, inputs and outputs. Positive synthetic outcomes validate mechanics, not economic viability. The real public replay processes 7,192 receipt-ordered observations from the disposable database; with unknown fees it recorded no legs or trades and explicit unknown-fee/staleness/skew rejections. A final 20.001-second forward smoke processed 683 observations, initialized the portfolio and recorded 734 rejected positive raw spreads with no execution. It is not a 14-day test.

`crossx.archive` performs dry-run or export of a closed receipt interval without deleting anything; verification checks SHA256, schema, count and ordering. Actual dry-run/export/verification agreed on 7,192 observations. Existing retention policy remains a read-only eligibility plan. No TTL, automatic volume removal or deletion command exists. Export is not a backup until an independent copy and its manifest are verified.

## Executed validation

- **146 pytest tests passed**, with all PostgreSQL fault/persistence/migration/session tests and actual Grafana security integration enabled; no skipped tests in that recorded run.
- Ruff passed. Strict mypy passed for 20 source files.
- Alembic upgrade and schema check passed; exact legacy-row preservation tested.
- Default and isolated/optional Compose configurations validated. Disposable application, storage, monitoring and notification images built/started.
- Promtool config validation and rule tests passed: 13 alert rules, including deterministic new unhedged/drawdown firing scenarios and all prior scenarios.
- Grafana anonymous dashboard access succeeded; anonymous and admin/admin admin APIs were denied. All 33 dashboard panels provisioned; Prometheus targets were up.
- Actual local Alertmanager→webhook delivery returned HTTP 200 and appears in `final-alert-delivery.txt`. The official current Alertmanager v0.34.2 image is digest pinned and amtool validates its config. External paid/email/chat delivery is not configured.
- Disposable app gracefully stopped with exit code 0 and returned to readiness in an 11.465-second stop/start check. This check occurred outside the timed ingestion window.
- Four-adapter bounded public probes passed: Kraken 3,151 BTC/192 ETH; Coinbase 312/246; Crypto.com 87/54; Binance 6/2; 20-second requested sustained minimum, zero reconnects/integrity errors. Per-adapter completion can exceed that minimum while waiting for a sparse symbol.

Files and exact commands are in `docs/evidence/phases2-7/commands.sh`; raw results, source/image hashes, migration checks, reports and checksum manifest accompany this report. Earlier developmental observations/scenarios are preserved and distinguishable from `final-*` outputs. No unexecuted long soak or future acceptance is claimed.

## Performance and observed availability

The initial 120.004-second sample has a startup metrics HTTP failure at sample 0 and is explicitly **incomplete**. Raw samples are retained. A separate readiness-gated window ran **2026-11-08 10:00:17.780 UTC to 10:01:17.780 UTC**, 59.9997 seconds, seven samples, no collection errors:

| Pair | New SQL observations |
|---|---:|
| Kraken BTC / ETH | 9,812 / 2,520 |
| Coinbase BTC / ETH | 805 / 820 |
| Binance BTC / ETH | 7 / 34 |
| Crypto.com BTC / ETH | 352 / 300 |

Total 14,650, mean 244.17 writes/s, queue maximum **sampled** 39/128, mean per-book commit 7.906ms. Counter/SQL differences of a few rows reflect sequential collection, not atomic measurement. No reconnect/integrity counter increments or unexpected restarts occurred in this window. Readiness stayed 1; synchronization samples include 0 during initial Binance bridge establishment and 1 thereafter. Binance BTC latest receipt age reached 15.70 seconds: native USD activity is sparse and the source cannot be assumed fresh continuously. Stale source clocks remain ineligible for hypothetical opportunities. Unknown fees kept persisted opportunities at zero while all venues collected data.

App CPU mean/max 31.95/38.82%, maximum memory 87.56MB; PostgreSQL CPU 11.62/19.53%, maximum memory 156.97MB. Full per-service resources are retained. These are short measurements on a shared local Docker host, including concurrent original services and some test activity. They are not a fair speed comparison to prior different market windows.

The controlled benchmark separately processes the same 8,192 synthetic depth-25 frames in detector-only and full capital-paper modes, with traced Python allocations. Final results: detector 1,566.70 frames/s, mean 0.637ms, p95 1.035ms; paper 784.34 frames/s, mean 1.274ms, p95 1.894ms, traced peak 278,972 bytes. The paper mode does more work; no throughput improvement is claimed. This excludes DB/network/event-output I/O and uses a synthetic arrival schedule. Retain real-data throughput measurements independently.

Book storage grew 16,121,856 bytes in the warmed minute, extrapolating **23.22GB/day**, around **325GB for 14 days** before other retained data/headroom. WAL generation extrapolated to 25.66GB/day, not necessarily retained WAL. Default 20GiB/day and 30GiB dataset budgets are warnings; the measured book rate exceeds the daily budget and would exhaust a 30GiB dataset allocation in roughly 1.4 days. Fourteen-day readiness therefore requires reviewed capacity/archive/sampling policy. Do not solve it by automatic deletion or silent sampling. Sampling reduces historical resolution and cannot support lossless replay claims.

## Security review and remaining acceptance gaps

Trivy 0.74.0 scanned locked dependencies and actual local images for HIGH/CRITICAL package matches. Python lockfile: 0/0. Final app OS: 49 HIGH/0 CRITICAL; Python packages in that image: 0. Grafana: 54/0; Prometheus: 2/0; PostgreSQL: 28/1. The PostgreSQL critical match is CVE-2025-68121 in the Go stdlib of `gosu`, not a demonstrated SQL-engine exploit. Reachability/exploitability was not established. Do not dismiss a package finding or declare the stack vulnerability-free.

The initially considered Alertmanager v0.28.1 scanned at 97 HIGH/2 CRITICAL. It was replaced by official current v0.34.2, digest pinned; its rescan reports 2 HIGH/0 CRITICAL. Original and final reports are preserved. Remaining findings need version/patch triage before prolonged unattended operation. CI retains read-only permissions and disabled checkout credential persistence; no scanner finding was suppressed or safety test disabled. Optional webhook sink has no host-published port; Alertmanager binds an explicit loopback port. No secrets are embedded.

Known incomplete or limited capabilities:

- No minimum 14-day evaluation or long four-venue soak completed; long-duration operational acceptance remains incomplete.
- No automatic mid-session recovery/resume of simulated balances; distinct failed/restarted sessions must be reconciled explicitly.
- Forward receipt-window completeness assumes a single ordered writer and adequate commit lag. Its new-id late-insert guard cannot prove completeness under concurrent out-of-order commits using previously allocated ids. Closed exported replay is the stronger evidence path.
- Phase 1 history lacks feed-generation boundaries, individual orders, full unlimited depth and exact transport-arrival timing. It cannot prove uninterrupted feed health, queue priority, market impact or actual fills.
- Rebalancing is a requirement/cost sensitivity, not a simulated timed transfer/replenishment workflow. Capital remains pre-funded; depleted inventory is rejected.
- Cross-quote conversion is unevaluable and disabled; account eligibility and actual fee tiers remain unverified by design.
- Monitoring exposes the latest completed session, not each concurrent session or live in-progress portfolio. Session evidence is complete in DB/files; a multi-session operations view is not implemented.
- Dataset/index growth, stale sparse native markets, inherited image vulnerabilities and nonresumable forward sessions block unattended 14-day sign-off.

`FORWARD_EVALUATION.md` defines a separate minimum 14-day protocol and measurable go/no-go criteria covering integrity, coverage, reconciliation, fees/slippage, failed legs, depletion/rebalancing, P&L/drawdown, storage, notifications and outage evidence. Profit is not an acceptance target. Human review and operational approval are required before starting it. Live trading remains outside scope.

Large raw evidence is committed as deterministic gzip, with original and compressed SHA256 values in `archive-manifest.json`; local uncompressed originals remain preserved and ignored by Git. To inspect, use `gzip -dc FILE.gz`; extract to a new directory when validating a replay's original manifest. Earlier exploratory outputs are retained, not substituted for final measurements. `preservation.json` and the before/after metadata confirm the original stack remained unchanged; `disposable-stop.txt` records only the scoped disposable stop. All new volumes are retained.
