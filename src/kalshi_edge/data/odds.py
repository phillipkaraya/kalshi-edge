"""The Odds API adapter -> devigged consensus probabilities per NBA game.

We request decimal h2h (moneyline) odds across US books, devig each book's two
prices, and keep the per-book devigged probabilities so downstream code can
compute a consensus + dispersion. Needs ODDS_API_KEY; without it the caller
falls back to the demo fixture.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from ..config import Settings, get_settings
from .devig import decimal_to_prob, devig_two_way
from .teams import resolve_team

ODDS_API_BASE = "https://api.the-odds-api.com/v4"


class OddsConfigError(RuntimeError):
    """Raised when an odds provider is used without an API key."""


class _Outcome(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    price: float


class _Market(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    outcomes: list[_Outcome] = []


class _Bookmaker(BaseModel):
    model_config = ConfigDict(extra="ignore")
    key: str
    title: str | None = None
    markets: list[_Market] = []


class OddsGame(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: list[_Bookmaker] = []


@dataclass(frozen=True)
class GameOdds:
    """Per-book devigged probabilities for one game (tricodes resolved)."""

    home: str
    away: str
    commence_time: datetime | None
    home_book_probs: list[float]
    away_book_probs: list[float]
    source: str = "odds_api"


def transform_odds_games(games: list[OddsGame]) -> list[GameOdds]:
    """Devig each book's h2h prices and collect per-book probabilities by game."""
    out: list[GameOdds] = []
    for g in games:
        home_tri = resolve_team(g.home_team)
        away_tri = resolve_team(g.away_team)
        if not (home_tri and away_tri):
            continue
        home_probs: list[float] = []
        away_probs: list[float] = []
        for book in g.bookmakers:
            h2h = next((m for m in book.markets if m.key == "h2h"), None)
            if not h2h:
                continue
            p_home = p_away = None
            for o in h2h.outcomes:
                if o.price <= 1.0:
                    continue
                tri = resolve_team(o.name)
                if tri == home_tri:
                    p_home = decimal_to_prob(o.price)
                elif tri == away_tri:
                    p_away = decimal_to_prob(o.price)
            if p_home is not None and p_away is not None:
                dh, da = devig_two_way(p_home, p_away)
                home_probs.append(dh)
                away_probs.append(da)
        if home_probs and away_probs:
            out.append(GameOdds(home_tri, away_tri, g.commence_time, home_probs, away_probs))
    return out


class OddsApiClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._key = self.settings.odds_api_key

    def get_raw_nba(self) -> list[OddsGame]:
        if not self._key:
            raise OddsConfigError("ODDS_API_KEY not set")
        params = {"regions": "us", "markets": "h2h", "oddsFormat": "decimal", "apiKey": self._key}
        with httpx.Client(base_url=ODDS_API_BASE, timeout=httpx.Timeout(20.0)) as client:
            resp = client.get("/sports/basketball_nba/odds", params=params)
            resp.raise_for_status()
            data = resp.json()
        return [OddsGame.model_validate(g) for g in data]

    def get_game_odds(self) -> list[GameOdds]:
        return transform_odds_games(self.get_raw_nba())


def get_game_odds_cached(
    settings: Settings | None = None,
    *,
    cache_path: str | Path = "data/odds_cache.json",
) -> tuple[list[GameOdds], bool]:
    """Odds with a shared on-disk cache so the board and the scheduled paper pass
    share one fetch within the TTL (protects the Odds API free-tier quota).

    Returns ``(games, from_cache)``. Serves a stale cache if a live fetch fails.
    """
    settings = settings or get_settings()
    path = Path(cache_path)
    if path.exists() and (time.time() - path.stat().st_mtime) < settings.odds_cache_ttl_seconds:
        raw = [OddsGame.model_validate(g) for g in json.loads(path.read_text())]
        return transform_odds_games(raw), True
    try:
        games_raw = OddsApiClient(settings).get_raw_nba()
    except Exception:
        if path.exists():  # serve stale rather than fail
            raw = [OddsGame.model_validate(g) for g in json.loads(path.read_text())]
            return transform_odds_games(raw), True
        raise
    try:  # best-effort cache; a read-only FS (e.g. Streamlit Cloud) still serves fresh odds
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([g.model_dump(mode="json") for g in games_raw]))
    except OSError:
        pass
    return transform_odds_games(games_raw), False
