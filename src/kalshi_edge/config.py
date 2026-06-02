"""Application settings, loaded from environment / .env.

Kalshi exposes two environments: production (real money) and a demo sandbox.
Market-data reads are public on both; only trading endpoints need credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROD_HOST = "https://api.elections.kalshi.com"
DEMO_HOST = "https://demo-api.kalshi.co"
API_PREFIX = "/trade-api/v2"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Kalshi --------------------------------------------------------------
    kalshi_env: Literal["prod", "demo"] = "prod"
    kalshi_key_id: str | None = None
    kalshi_private_key_path: Path | None = None
    # NBA series: KXNBAGAME = per-game winners (core unit); KXNBA = Finals futures.
    kalshi_series: list[str] = Field(default_factory=lambda: ["KXNBAGAME"])

    # --- Sports data (Slice 1+) ---------------------------------------------
    odds_api_key: str | None = None
    balldontlie_api_key: str | None = None
    data_tier: Literal["free", "paid"] = "free"
    odds_cache_ttl_seconds: int = 3600  # shared file-cache TTL for Odds API (protects free quota)

    # --- Edge / sizing (Slice 1+) -------------------------------------------
    bankroll: float = 1000.0  # paper bankroll for Kelly sizing
    kelly_fraction: float = 0.25  # fraction of full Kelly (0.25 = quarter-Kelly)
    max_position_fraction: float = 0.05  # hard cap: max share of bankroll per market
    min_ev: float = 0.01  # only surface edges with net EV >= this (per contract, $)
    fee_multiplier: float = 0.07  # Kalshi taker fee multiplier (sports/standard, Feb 2026)

    # --- Storage -------------------------------------------------------------
    db_path: Path = Path("data/kalshi_edge.db")

    # --- Execution / risk (Slice 2+) ----------------------------------------
    execution_mode: Literal["paper", "demo", "live"] = "paper"
    max_contracts_per_market: int = 200
    max_total_exposure_fraction: float = 0.5  # max share of bankroll deployed at once
    max_event_exposure_fraction: float = 0.06  # max share of bankroll on any single game (event)
    max_daily_loss: float = 100.0  # stop trading once realized daily loss hits this ($)
    min_liquidity_spread: float = 0.05  # skip markets whose YES spread is wider than this
    kill_switch: bool = False  # hard stop: blocks all order placement when True
    live_enabled: bool = False  # live stays OFF until the Slice 3 consistency gate flips it

    @property
    def has_odds_source(self) -> bool:
        """True when a live odds provider key is configured (else fixture mode)."""
        return bool(self.odds_api_key) or bool(self.balldontlie_api_key)

    @property
    def kalshi_host(self) -> str:
        return DEMO_HOST if self.kalshi_env == "demo" else PROD_HOST

    def api_path(self, endpoint: str) -> str:
        """Full server path for an endpoint, e.g. ``markets`` -> ``/trade-api/v2/markets``.

        This is also the exact string signed for authenticated requests, so it
        must never include the query string.
        """
        return f"{API_PREFIX}/{endpoint.lstrip('/')}"


def get_settings() -> Settings:
    """Construct settings from the environment. Cheap; safe to call per-request."""
    return Settings()
