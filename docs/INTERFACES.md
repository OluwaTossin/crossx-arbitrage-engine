# Shared Phase 1 interfaces

Python package `crossx` under `src/`. Public research only; no order or credential code.

Foundation owns schemas.py, config.py, db.py, alembic/, pyproject.toml, Dockerfile, compose.yaml, .github/workflows/ci.yml, .env.example.
Adapters owns adapters/* and tests/test_adapters.py, tests/fixtures/*, scripts/check_connectivity.py.
Intelligence owns intelligence.py and tests/test_intelligence.py.
Lead owns app.py, observability.py, other tests, monitoring/, README, architecture and acceptance reports.

Canonical Pydantic v2 frozen schemas:
- Exchange = Literal['kraken', 'coinbase']; Symbol = Literal['BTC/USD', 'ETH/USD']
- Level(price: Decimal, quantity: Decimal), both positive and finite.
- OrderBook(exchange: Exchange, symbol: Symbol, bids: tuple[Level,...], asks: tuple[Level,...], observed_at: datetime, received_at: datetime, synchronized: bool=True). UTC aware timestamps, sorted unique levels, nonempty, uncrossed. observed_at is event timestamp when available; snapshots may use receive time.
- FeeSchedule(maker: Decimal | None=None, taker: Decimal | None=None), fractional nonnegative finite rates; unknown is None.
- Opportunity(symbol, buy_exchange, sell_exchange, quantity, buy_vwap, sell_vwap, gross_profit, buy_fee, sell_fee, net_profit: Decimal fields, observed_at: datetime). Additional default fields permitted.

Configuration Settings via pydantic-settings: database_url string (async SQLAlchemy postgresql+asyncpg), symbols tuple default BTC/USD ETH/USD; fees dict[Exchange, FeeSchedule] with unknown fees; trade_quantity_btc Decimal .001, trade_quantity_eth Decimal .01; max_book_age_seconds float 5, max_book_skew_seconds float 2, min_net_profit Decimal 0, metrics_port int 8000. JSON environment config via CROSSX_ prefix. Example explicit fee estimates labeled assumptions, not current exchange rates.

Persistence async Database(database_url), async initialize() (connectivity check; Alembic owns DDL), async save_book(book), async save_opportunity(opportunity), async close(). Store Decimal without float conversion. Bound SQLAlchemy numeric precision documented.

Adapters expose KrakenAdapter(symbols: tuple[Symbol,...]) and CoinbaseAdapter(symbols), async stream() -> AsyncIterator[OrderBook]. Reset all state on disconnect/integrity error; timeout + bounded reconnect backoff. Tests should expose synchronous parsers via .process(message: dict, received_at: datetime) -> list[OrderBook]. IntegrityError custom in adapters base. Never emit invalid books. Protocol-level heartbeat kept distinct from market observation freshness.

Intelligence exposes detect_opportunities(books: list[OrderBook], fees: dict[Exchange,FeeSchedule], quantity: Decimal, now: datetime, max_age_seconds: float=5, max_skew_seconds: float=2, min_net_profit: Decimal=Decimal('0')) -> list[Opportunity]. Taker VWAP both directions; unknown relevant fee rejects; stale/future/unsync/skew books reject; complete depth only. No execution.
