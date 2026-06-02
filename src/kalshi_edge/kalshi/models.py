"""Pydantic models for Kalshi API payloads.

Field aliases map Kalshi's wire format onto clean float attributes:
  * prices arrive as dollar-strings, e.g. ``yes_bid_dollars: "0.4700"``
  * sizes/volume arrive as floats with an ``_fp`` suffix, e.g. ``volume_fp``
Prices are in dollars on a 0.0-1.0 scale, which is also the implied probability.
``extra="ignore"`` keeps us resilient to Kalshi adding fields.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(cast("str | float | int", value))
    except (TypeError, ValueError):
        return None


class Market(BaseModel):
    """A single Kalshi binary market (e.g. one NBA game's "Team X wins" contract)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    ticker: str
    event_ticker: str
    title: str | None = None
    yes_sub_title: str | None = None
    no_sub_title: str | None = None
    status: str | None = None
    result: str | None = None

    yes_bid: float | None = Field(default=None, alias="yes_bid_dollars")
    yes_ask: float | None = Field(default=None, alias="yes_ask_dollars")
    no_bid: float | None = Field(default=None, alias="no_bid_dollars")
    no_ask: float | None = Field(default=None, alias="no_ask_dollars")
    last_price: float | None = Field(default=None, alias="last_price_dollars")

    volume: float | None = Field(default=None, alias="volume_fp")
    volume_24h: float | None = Field(default=None, alias="volume_24h_fp")
    open_interest: float | None = Field(default=None, alias="open_interest_fp")
    liquidity: float | None = Field(default=None, alias="liquidity_dollars")

    # Order-book sizes + previous prices -> momentum / line-movement signals (Slice 4).
    yes_bid_size: float | None = Field(default=None, alias="yes_bid_size_fp")
    yes_ask_size: float | None = Field(default=None, alias="yes_ask_size_fp")
    previous_yes_bid: float | None = Field(default=None, alias="previous_yes_bid_dollars")
    previous_yes_ask: float | None = Field(default=None, alias="previous_yes_ask_dollars")
    previous_price: float | None = Field(default=None, alias="previous_price_dollars")

    open_time: datetime | None = None
    close_time: datetime | None = None

    @field_validator(
        "yes_bid",
        "yes_ask",
        "no_bid",
        "no_ask",
        "last_price",
        "liquidity",
        "volume",
        "volume_24h",
        "open_interest",
        "yes_bid_size",
        "yes_ask_size",
        "previous_yes_bid",
        "previous_yes_ask",
        "previous_price",
        mode="before",
    )
    @classmethod
    def _coerce_float(cls, value: object) -> float | None:
        return _to_float(value)

    @property
    def mid_yes(self) -> float | None:
        """Midpoint of the YES bid/ask, in dollars (= probability)."""
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return round((self.yes_bid + self.yes_ask) / 2, 4)

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return round(self.yes_ask - self.yes_bid, 4)

    @property
    def implied_prob(self) -> float | None:
        """Market-implied probability of YES, from the bid/ask midpoint."""
        return self.mid_yes

    @property
    def is_tradeable(self) -> bool:
        return self.status in {"active", "open"} and self.yes_ask is not None and self.yes_ask > 0


class MarketsPage(BaseModel):
    """A page of markets plus the pagination cursor."""

    model_config = ConfigDict(extra="ignore")

    cursor: str | None = None
    markets: list[Market] = Field(default_factory=list)
