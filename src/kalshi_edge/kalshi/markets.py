"""Fetch and filter Kalshi markets for one or more series (e.g. NBA games)."""

from __future__ import annotations

from .client import KalshiClient
from .models import Market

_MAX_PAGES = 20  # safety cap against runaway pagination


def fetch_markets(
    client: KalshiClient,
    series_tickers: list[str],
    *,
    status: str = "open",
) -> list[Market]:
    """Fetch all markets across the given series, following pagination cursors."""
    out: list[Market] = []
    for series in series_tickers:
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            page = client.get_markets(series_ticker=series, status=status, limit=200, cursor=cursor)
            out.extend(page.markets)
            cursor = page.cursor
            if not cursor or not page.markets:
                break
    return out


def tradeable_markets(markets: list[Market]) -> list[Market]:
    """Keep only markets that are active and actually quotable."""
    return [m for m in markets if m.is_tradeable]
