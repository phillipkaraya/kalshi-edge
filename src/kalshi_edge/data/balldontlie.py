"""BALLDONTLIE adapter for NBA injuries + a fair-value injury nudge.

In the NBA a star ruled out can swing a team's win probability 10+ points, and
Kalshi often lags the news. This adapter pulls current injuries (needs a key);
``injury_nudge`` shifts a fair probability when a key player's status changes.

Mapping an injury to the right team + direction + magnitude needs roster/star
data, which is a v1.1 refinement -- here we ship the building blocks (adapter +
status weights + nudge) with the wiring left conservative.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import Settings, get_settings

BALLDONTLIE_BASE = "https://api.balldontlie.io/v1"

# Win-probability magnitude by injury status (applied with a sign by the caller).
_STATUS_WEIGHTS = {"out": 0.06, "doubtful": 0.04, "questionable": 0.015, "probable": 0.005}


class BalldontlieConfigError(RuntimeError):
    """Raised when the adapter is used without an API key."""


@dataclass(frozen=True)
class Injury:
    player: str
    status: str


class BalldontlieClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._key = self.settings.balldontlie_api_key

    def get_injuries(self) -> list[Injury]:
        if not self._key:
            raise BalldontlieConfigError("BALLDONTLIE_API_KEY not set")
        with httpx.Client(
            base_url=BALLDONTLIE_BASE,
            timeout=httpx.Timeout(20.0),
            headers={"Authorization": self._key},
        ) as client:
            resp = client.get("/player_injuries")
            resp.raise_for_status()
            data = resp.json().get("data", [])
        injuries: list[Injury] = []
        for item in data:
            player = item.get("player") or {}
            name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
            injuries.append(Injury(player=name, status=str(item.get("status", ""))))
        return injuries


def status_delta(status: str) -> float:
    """Win-prob magnitude for a status string (0 if unknown). Caller applies the sign."""
    return _STATUS_WEIGHTS.get(status.strip().lower(), 0.0)


def injury_nudge(p_fair: float, *, key_player_out_delta: float) -> float:
    """Shift YES fair probability by a (signed) delta, clamped to (0.01, 0.99).

    Negative lowers the YES team's win prob (their star is out); positive raises it
    (the opponent's star is out).
    """
    return round(min(0.99, max(0.01, p_fair + key_player_out_delta)), 4)
