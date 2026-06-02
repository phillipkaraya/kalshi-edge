"""NBA team registry + name resolution + Kalshi market parsing.

Kalshi refers to teams by city ("San Antonio") in titles/subtitles; sportsbook
APIs use full names ("San Antonio Spurs"). We resolve both to a canonical
tricode so a Kalshi market can be matched to the right game's odds.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Team:
    tricode: str
    city: str
    nickname: str

    @property
    def full(self) -> str:
        return f"{self.city} {self.nickname}"


_TEAMS: list[Team] = [
    Team("ATL", "Atlanta", "Hawks"),
    Team("BOS", "Boston", "Celtics"),
    Team("BKN", "Brooklyn", "Nets"),
    Team("CHA", "Charlotte", "Hornets"),
    Team("CHI", "Chicago", "Bulls"),
    Team("CLE", "Cleveland", "Cavaliers"),
    Team("DAL", "Dallas", "Mavericks"),
    Team("DEN", "Denver", "Nuggets"),
    Team("DET", "Detroit", "Pistons"),
    Team("GSW", "Golden State", "Warriors"),
    Team("HOU", "Houston", "Rockets"),
    Team("IND", "Indiana", "Pacers"),
    Team("LAC", "Los Angeles", "Clippers"),
    Team("LAL", "Los Angeles", "Lakers"),
    Team("MEM", "Memphis", "Grizzlies"),
    Team("MIA", "Miami", "Heat"),
    Team("MIL", "Milwaukee", "Bucks"),
    Team("MIN", "Minnesota", "Timberwolves"),
    Team("NOP", "New Orleans", "Pelicans"),
    Team("NYK", "New York", "Knicks"),
    Team("OKC", "Oklahoma City", "Thunder"),
    Team("ORL", "Orlando", "Magic"),
    Team("PHI", "Philadelphia", "76ers"),
    Team("PHX", "Phoenix", "Suns"),
    Team("POR", "Portland", "Trail Blazers"),
    Team("SAC", "Sacramento", "Kings"),
    Team("SAS", "San Antonio", "Spurs"),
    Team("TOR", "Toronto", "Raptors"),
    Team("UTA", "Utah", "Jazz"),
    Team("WAS", "Washington", "Wizards"),
]
TEAMS: dict[str, Team] = {t.tricode: t for t in _TEAMS}

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", "".join(c for c in s.lower() if c.isalnum() or c == " ")).strip()


# Exact-match alias index. Bare city is only indexed when unique (so "Los Angeles"
# stays ambiguous and falls through to nickname matching for LAL/LAC).
_city_counts = Counter(_norm(t.city) for t in _TEAMS)
_ALIAS: dict[str, str] = {}
for _t in _TEAMS:
    _ALIAS[_norm(_t.full)] = _t.tricode
    _ALIAS[_norm(_t.nickname)] = _t.tricode
    _ALIAS[_norm(_t.tricode)] = _t.tricode
    if _city_counts[_norm(_t.city)] == 1:
        _ALIAS[_norm(_t.city)] = _t.tricode
_ALIAS[_norm("LA Lakers")] = "LAL"
_ALIAS[_norm("LA Clippers")] = "LAC"


def resolve_team(name: str | None) -> str | None:
    """Resolve a city / nickname / full name / tricode to a canonical tricode."""
    if not name:
        return None
    n = _norm(name)
    if n in _ALIAS:
        return _ALIAS[n]
    tokens = set(n.split())
    for tri, t in TEAMS.items():  # unique nicknames -> safe token match
        if _norm(t.nickname) in tokens:
            return tri
    for tri, t in TEAMS.items():  # contained city, skipping ambiguous Los Angeles
        city = _norm(t.city)
        if city and city in n and _city_counts[city] == 1:
            return tri
    return None


@dataclass(frozen=True)
class ParsedGame:
    away: str  # tricode
    home: str  # tricode
    yes_tricode: str  # tricode of the team the YES side backs
    game_date: date | None


def parse_ticker_date(ticker: str) -> date | None:
    """Parse the YYMONDD date embedded in a Kalshi game ticker (e.g. -26JUN10...)."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    if mon not in _MONTHS:
        return None
    try:
        return date(2000 + int(yy), _MONTHS[mon], int(dd))
    except ValueError:
        return None


def parse_kalshi_game(
    title: str | None, yes_sub_title: str | None, ticker: str
) -> ParsedGame | None:
    """Extract (away, home, yes-team, date) from a Kalshi game market.

    Titles look like "Game 4: San Antonio at New York Winner?" or "A vs B winner?".
    "X at Y" => X away, Y home. The YES team comes from ``yes_sub_title``.
    """
    if not title:
        return None
    t = re.sub(r"^\s*game\s+\d+\s*:\s*", "", title, flags=re.I)
    t = re.sub(r"\s*winner\??\s*$", "", t, flags=re.I).strip()
    parts = re.split(r"\s+(?:at|vs\.?)\s+", t, flags=re.I)
    if len(parts) != 2:
        return None
    away = resolve_team(parts[0])
    home = resolve_team(parts[1])
    yes = resolve_team(yes_sub_title)
    if not (away and home and yes):
        return None
    return ParsedGame(away=away, home=home, yes_tricode=yes, game_date=parse_ticker_date(ticker))
