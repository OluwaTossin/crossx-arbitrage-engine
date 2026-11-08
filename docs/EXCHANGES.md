# Public spot adapters and market identity

On 2026-11-08 the official Binance `GET https://data-api.binance.vision/api/v3/exchangeInfo` response listed BTCUSD and ETHUSD as TRADING, spot allowed, quoteAsset USD. Crypto.com `GET https://api.crypto.com/exchange/v1/public/get-instruments` listed BTC_USD and ETH_USD as tradable CCY_PAIR instruments. Full filtered responses, including rejected derivatives for auditability, are saved in `docs/evidence/phases2-7/markets.json`. The adapter allowlist contains only spot pairs; derivative instrument names cannot pass it. This is a dated discovery result, not a perpetual availability guarantee.

| Venue | BTC/USD | ETH/USD | Integrity | Transport |
|---|---|---|---|---|
| Kraken | BTC/USD (v2 naming) | ETH/USD | Existing top-ten CRC32, depth 25 | Existing public v2 WS |
| Coinbase | BTC-USD | ETH-USD | Existing connection sequence, snapshot/update ordering | Existing Advanced Trade level2 |
| Binance | BTCUSD | ETHUSD | REST lastUpdateId bridge, U/u diff continuity | Market-data-only WS and REST |
| Crypto.com | BTC_USD | ETH_USD | Full snapshot then pu/u delta chaining | Public market WS, JSON heartbeat response |

No USD/USDT/USDC parity is assumed. The canonical symbol encodes the actual quote asset. Optional native USDT/USDC collection is adapter-specific; neither the USD detector nor paper ledger treats it as USD. Cross-quote conversion is deliberately unsupported: a defensible executable conversion market, depth, fees and basis model has not been established. Such candidates are rejected, not relabelled.

Binance subscribes before requesting REST depth snapshots. The WebSocket receive buffer remains bounded at 16 frames while REST is fetched; obsolete diffs are dropped and a gap forces reconnection and new snapshots. The first accepted diff spans snapshot lastUpdateId+1. No timestamp is manufactured for a REST snapshot; it is not emitted as a book. Observed top-25 depth must remain within the proven REST price range. Crossing that coverage or the 20,000-level-per-side state limit forces resynchronization. This favors omission under large bursts over weakening correctness. Binance does not document a CRC for this JSON depth feed.

Crypto.com requests depth 50 SNAPSHOT_AND_UPDATE at 100ms and emits top 25. Every delta must link `pu` to the last `u`; missing snapshots, malformed prices and sequence gaps invalidate the connection generation. `public/heartbeat` receives only a `public/respond-heartbeat` response; it never refreshes market freshness. No CRC is documented for this channel. All adapters inherit bounded socket buffering, timeouts, capped reconnect backoff, Decimal decoding and cancellation cleanup. Kraken checksum code is unchanged.

Official specifications checked during implementation:

- https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/web-socket-streams.md
- https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints
- https://exchange-developer.crypto.com/exchange/v1/docs/api/websocket-single-page/
- https://exchange-developer.crypto.com/exchange/v1/docs/api/rest-single-page/

## UK eligibility and availability

Public market-data reachability does not establish account eligibility, regulatory approval or permission to trade. This implementation uses no accounts or private APIs. Exchange country restrictions and onboarding conditions must be checked separately on the official venue sites before any future proposal involving trading. No eligibility inference is made from the successful UK-host public probes. There is no live-trading capability in this project.

Official UK context checked 2026-11-08: Binance's [UK update](https://www.binance.com/en-GB/blog/all/update-on-binance-for-uk-users-937596818854026589) announces suspension of new UK onboarding from October 2023; this historical notice does not establish present individual eligibility. Crypto.com's [UK Exchange FPS guidance](https://help.crypto.com/en/articles/10145913-uk-retail-users-gbp-fiat-deposit-and-withdrawal-via-fps-exchange) describes a UK retail funding feature, subject to verification. Neither page substitutes for current account/product eligibility checks. No account onboarding or funding was attempted.
