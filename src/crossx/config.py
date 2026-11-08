"""Environment-only research configuration; no exchange credentials exist."""

from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from crossx.schemas import Exchange, FeeSchedule, Symbol


def unknown_fees() -> dict[Exchange, FeeSchedule]:
    venues: tuple[Exchange, ...] = ("kraken", "coinbase", "binance", "cryptocom")
    return {name: FeeSchedule() for name in venues}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CROSSX_", extra="ignore")

    database_url: str = "postgresql+asyncpg://crossx:crossx@localhost:5432/crossx"
    exchanges: tuple[Exchange, ...] = ("kraken", "coinbase", "binance", "cryptocom")
    symbols: tuple[Symbol, ...] = ("BTC/USD", "ETH/USD")
    fees: dict[Exchange, FeeSchedule] = Field(default_factory=unknown_fees)
    trade_quantity_btc: Decimal = Field(default=Decimal("0.001"), gt=0, allow_inf_nan=False)
    trade_quantity_eth: Decimal = Field(default=Decimal("0.01"), gt=0, allow_inf_nan=False)
    max_book_age_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    max_book_skew_seconds: float = Field(default=2, ge=0, allow_inf_nan=False)
    min_net_profit: Decimal = Field(default=Decimal("0"), ge=0, allow_inf_nan=False)
    persistence_timeout_seconds: float = Field(default=10, gt=0, allow_inf_nan=False)
    shutdown_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
    persistence_batch_size: int = Field(default=32, ge=1, le=128)
    book_sample_interval_seconds: float = Field(default=0, ge=0, allow_inf_nan=False)
    retention_days: int | None = Field(default=None, ge=1)
    daily_book_budget_gib: float = Field(default=20, gt=0, allow_inf_nan=False)
    storage_budget_gib: float = Field(default=30, gt=0, allow_inf_nan=False)
    disk_guard_path: str | None = None
    disk_headroom_gib: float = Field(default=5, gt=0, allow_inf_nan=False)
    disk_critical_gib: float = Field(default=3, gt=0, allow_inf_nan=False)
    capacity_run_seconds: int = Field(default=0, ge=0)
    capacity_daily_gb: float = Field(default=23.22, gt=0, allow_inf_nan=False)
    capacity_safety_factor: float = Field(default=1.3, ge=1, allow_inf_nan=False)
    metrics_port: int = Field(default=8000, ge=1, le=65535)

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use postgresql+asyncpg")
        return value

    @field_validator("exchanges")
    @classmethod
    def venues_unique(cls, value: tuple[Exchange, ...]) -> tuple[Exchange, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("exchanges must be nonempty and unique")
        return value

    @field_validator("symbols")
    @classmethod
    def symbols_unique(cls, value: tuple[Symbol, ...]) -> tuple[Symbol, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("symbols must be nonempty and unique")
        return value
