"""Tests for Kalshi payload parsing -- built from a real NBA Finals market."""

from __future__ import annotations

from kalshi_edge.kalshi.models import Market, MarketsPage

# Captured live from the API: Game 4 of the 2026 NBA Finals (SAS @ NYK).
SAMPLE = {
    "ticker": "KXNBAGAME-26JUN10SASNYK-SAS",
    "event_ticker": "KXNBAGAME-26JUN10SASNYK",
    "title": "Game 4: San Antonio at New York Winner?",
    "yes_sub_title": "San Antonio",
    "yes_bid_dollars": "0.4700",
    "yes_ask_dollars": "0.4800",
    "no_bid_dollars": "0.5200",
    "no_ask_dollars": "0.5300",
    "last_price_dollars": "0.4800",
    "volume_fp": 1234.0,
    "status": "active",
}


def test_market_parses_dollar_strings() -> None:
    m = Market.model_validate(SAMPLE)
    assert m.yes_bid == 0.47
    assert m.yes_ask == 0.48
    assert m.implied_prob == 0.475
    assert m.spread == 0.01
    assert m.volume == 1234.0
    assert m.is_tradeable


def test_market_handles_missing_prices() -> None:
    m = Market.model_validate({"ticker": "X-Y-Z", "event_ticker": "X-Y", "status": "active"})
    assert m.implied_prob is None
    assert m.spread is None
    assert not m.is_tradeable  # no ask -> not quotable


def test_markets_page_ignores_extra_fields() -> None:
    page = MarketsPage.model_validate({"cursor": "abc", "markets": [SAMPLE], "unexpected_field": 1})
    assert page.cursor == "abc"
    assert len(page.markets) == 1
    assert page.markets[0].ticker.endswith("-SAS")
