"""Matching Kalshi markets to fair value via the demo fixture."""

from __future__ import annotations

from kalshi_edge.data.fixtures import fixture_game_odds
from kalshi_edge.data.matcher import fair_value_for_market
from kalshi_edge.kalshi.models import Market


def _market(title: str, yes_team: str, ticker: str) -> Market:
    return Market.model_validate(
        {
            "ticker": ticker,
            "event_ticker": ticker.rsplit("-", 1)[0],
            "title": title,
            "yes_sub_title": yes_team,
            "yes_bid_dollars": "0.60",
            "yes_ask_dollars": "0.61",
            "status": "active",
        }
    )


def test_home_team_fair_value() -> None:
    # New York AT San Antonio => SAS is home; fixture home prob ~0.65.
    m = _market(
        "Game 1: New York at San Antonio Winner?", "San Antonio", "KXNBAGAME-26JUN03NYKSAS-SAS"
    )
    fv = fair_value_for_market(m, fixture_game_odds())
    assert fv is not None
    assert fv.p_fair == 0.65  # median of [0.65, 0.66, 0.64]
    assert fv.n_books == 3
    assert fv.source == "FIXTURE"


def test_away_team_fair_value() -> None:
    # San Antonio AT New York => SAS is away; fixture away prob ~0.41.
    m = _market(
        "Game 4: San Antonio at New York Winner?", "San Antonio", "KXNBAGAME-26JUN10SASNYK-SAS"
    )
    fv = fair_value_for_market(m, fixture_game_odds())
    assert fv is not None
    assert fv.p_fair == 0.41  # median of [0.42, 0.40, 0.41]


def test_unmatched_market_returns_none() -> None:
    m = _market("Some Other Thing Winner?", "Atlanta", "KXNBAGAME-26JUN10ATLBOS-ATL")
    assert fair_value_for_market(m, fixture_game_odds()) is None
