"""Illustrative DEMO odds so the Edge Board renders fully without any API key.

These are NOT live. They model the current Finals matchup (SAS vs NYK) with a
home-court bump, in both orientations, so the matcher can attach a fair value to
live Kalshi games and exercise the full edge/EV/Kelly pipeline. Replace with
live odds by setting ODDS_API_KEY / BALLDONTLIE_API_KEY.
"""

from __future__ import annotations

from .odds import GameOdds


def fixture_game_odds() -> list[GameOdds]:
    return [
        # San Antonio at home: ~65%.
        GameOdds("SAS", "NYK", None, [0.65, 0.66, 0.64], [0.35, 0.34, 0.36], source="FIXTURE"),
        # New York at home: ~59% (so San Antonio on the road ~41%).
        GameOdds("NYK", "SAS", None, [0.58, 0.60, 0.59], [0.42, 0.40, 0.41], source="FIXTURE"),
    ]
