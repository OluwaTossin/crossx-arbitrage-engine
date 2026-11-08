# Capital-aware spot paper research

The existing ingestion process records public data. `crossx.replay` and `crossx.forward` are separate deterministic paper clients of persisted observations. They import no exchange transport. They never submit orders. They require an explicit scenario JSON file; `config/paper.example.json` is deliberately unevaluable until the researcher supplies fees and market rules. Unknown taker fees and missing rules fail closed.

## Assumptions and accounting

Starting GBP is converted once using explicit `gbp_usd` and `assumption_at`, reduced by configured FX and funding costs, then divided across the active venues. Equal allocation uses all configured venues. Selective allocation uses only `selected_venues`; it does not invent balances at unselected venues. Cash plus BTC/ETH inventory cost equals the funded USD pool, with no duplicated capital or borrowing. Initial crypto is a marked, pre-funded endowment, not a claim that historical market orders bought it. Initial endowment quantities can be fractional; subsequent simulated orders must meet configured venue increments and min/max quantities and notional. Rules and fees are scenario assumptions, not automatically verified account terms. Do not put exchange credentials in configuration.

The model reserves buy cash including adverse slippage and fees, and sell-venue base inventory. One pending trade per symbol prevents overlapping same-symbol signal execution. It checks capital, inventory, trade size, aggregate inventory concentration, venue exposure, expected net profit/return after the assumed rebalancing charge, portfolio loss and maximum drawdown limits. BTC and ETH reservations share the same cash pool. Orders use taker fees on both legs. Risk checks do not guarantee protection from adverse fills during modeled latency.

Execution is sequential: buy due after `latency_ms`, then sell after `second_leg_latency_ms`. Due legs execute before a newly arriving book with that receipt timestamp; future observations cannot improve an earlier fill. Fills consume displayed depth and adverse slippage, with quantity rounding and minimum-order checks again at execution. A price movement beyond the reserved cash fails the first leg rather than silently overspending. Missing/stale liquidity yields failure, not an invented fill. Partial buys reduce the second leg; a failed/partial sell leaves explicitly tracked unhedged base exposure. Configurable deterministic second-leg failure/fill fraction supports stress scenarios. No simultaneous-fill guarantee exists.

Consumed quantity persists at a price while it remains visible, even across timestamp/precision changes, quantity updates or changes at other levels. This conservative policy prevents repeated unchanged liquidity reuse. A price disappearing from exposed depth and later returning is treated as new visible liquidity; limited-depth observations cannot prove replenishment, queue position, hidden liquidity or real market impact. Observations do not describe our hypothetical trades' impact on actual subsequent feeds.

Results distinguish raw spread, fee/slippage-adjusted theoretical value, capital-feasible reservation, simulated leg outcomes and completed/unhedged trades. Weighted-average inventory cost records realised inventory-sale P&L. Paired cashflow P&L reports matched buy/sell quantities separately; it excludes unmatched costs and is not aggregate portfolio profit. NAV marks base holdings at the latest available bid. `trading_pnl_usd` is NAV change from initial marked portfolio; `total_pnl_usd` includes initial conversion/funding/spread costs relative to gross GBP valuation. Stale marks are listed and `valuation_complete` becomes false. These marks are not liquidation execution proofs. No liabilities exist because borrowing is prohibited.

Inventory moves only when a simulated leg settles on that venue. No instant inter-exchange transfers occur. Completed trades flag rebalancing requirements and the configured per-trade assumed cost; `paired_pnl_after_assumed_rebalance_usd` subtracts that cost for sensitivity analysis. The charge is also included conservatively in signal net thresholds. It is not a settled rebalance: no actual transfer workflow or replenishment is performed. Inventory depletion eventually rejects signals. A researcher must start a separately documented replenished scenario rather than silently resetting balances.

## Replay and forward mode

Database replay uses a repeatable-read, read-only server cursor ordered by `received_at,id`, in batches of 256. It preserves both exchange and receipt timestamps, and rejects receipt regressions. Source digests identify the exact canonical input stream. File replay requires the same ordering. Replay outputs an append-only session/event ledger, config/provenance, final balances, P&L and a hashed local report. Output directories must be new. Checkpoints append bounded event batches; events and final result commit atomically. A failed session has no completed result; automatic mid-session resume is not provided. Restart creates a distinct scenario, preserving prior evidence.

Forward mode is explicitly opt-in with a required bounded duration. It replays newly persisted receipt windows with a configurable commit lag and polls once per second. The simulated clock follows receipt time, delayed from wall clock; this is forward paper evaluation over recorded public data, not a real-time execution promise. Late newly committed ids behind the closed receipt cursor cause an incomplete session. This guard assumes a single ordered ingestion writer; concurrent out-of-order commits with previously allocated ids cannot be completely identified from Phase 1 schema. Use a dedicated collection database and commit lag above measured persistence stalls; review completeness independently. For strict forensic conclusions, replay a closed, exported, verified dataset.

Phase 1 histories lack reconnect-generation markers, exact socket-arrival times, unlimited depth and individual order identities. Books tagged synchronized at capture cannot prove uninterrupted synchronization between rows. This limitation remains explicit; missing history is not zero opportunity. No look-ahead interpolation or synthetic missing frames are added.

## Reproduction

```sh
# Supply fees and market rules as explicit, dated research assumptions first.
python -m crossx.replay --config config/paper.example.json --database \
  --start 2026-11-08T10:00:00+00:00 --end 2026-11-08T10:10:00+00:00 \
  --source-commit YOUR_TESTED_SHA --out /tmp/crossx-new-replay
python -m crossx.replay --config config/paper.example.json \
  --input /tmp/verified-export/books.jsonl --source-commit YOUR_TESTED_SHA \
  --out /tmp/crossx-new-file-replay
# Only when separately approved for a bounded supervised forward run:
python -m crossx.forward --config config/paper.example.json --duration 60 \
  --commit-lag 12 --source-commit YOUR_TESTED_SHA --out /tmp/crossx-new-forward
```

Run separate scenario files with capital_gbp 500, 1000 and 1500, and allocation equal/selective. Additional positive capital values are supported. The same closed input and assumptions produce identical events and numerical results; directory/session UUIDs are metadata, not financial inputs. Equal allocation fragments small balances across eight base inventories and four cash wallets; minimum notionals, fees, FX/funding cost and depleted sell inventory can prevent any executable trade even when raw spreads exist.
