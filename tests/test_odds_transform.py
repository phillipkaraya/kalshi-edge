"""The Odds API payload -> devigged GameOdds (the live-data path, no key needed)."""

from __future__ import annotations

import pytest

from kalshi_edge.data.odds import OddsGame, transform_odds_games

# Minimal but realistic payload: one game, two books, decimal h2h odds.
PAYLOAD = [
    {
        "id": "abc123",
        "commence_time": "2026-06-10T23:30:00Z",
        "home_team": "New York Knicks",
        "away_team": "San Antonio Spurs",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "New York Knicks", "price": 1.67},
                            {"name": "San Antonio Spurs", "price": 2.30},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "New York Knicks", "price": 1.70},
                            {"name": "San Antonio Spurs", "price": 2.20},
                        ],
                    }
                ],
            },
        ],
    }
]


def test_transform_devigs_each_book() -> None:
    games = [OddsGame.model_validate(g) for g in PAYLOAD]
    out = transform_odds_games(games)
    assert len(out) == 1
    go = out[0]
    assert go.home == "NYK"
    assert go.away == "SAS"
    assert len(go.home_book_probs) == 2
    # Pinnacle: raw home 1/1.67=0.599, away 1/2.30=0.435; devigged home ~0.579.
    assert go.home_book_probs[0] == pytest.approx(0.5794, abs=2e-3)
    # Each book's devigged pair sums to 1.0.
    assert go.home_book_probs[0] + go.away_book_probs[0] == pytest.approx(1.0)
