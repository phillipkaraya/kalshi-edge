"""Match a Kalshi NBA market to the corresponding game's devigged fair value."""

from __future__ import annotations

from datetime import UTC, datetime

from ..kalshi.models import Market
from ..model.probability import FairValue, fair_value
from .odds import GameOdds
from .teams import ParsedGame, parse_kalshi_game


def _match_game(parsed: ParsedGame, games: list[GameOdds]) -> GameOdds | None:
    pair = frozenset({parsed.away, parsed.home})
    candidates = [g for g in games if frozenset({g.home, g.away}) == pair]
    if parsed.game_date:
        candidates = [
            g
            for g in candidates
            if g.commence_time is None or abs((g.commence_time.date() - parsed.game_date).days) <= 1
        ]
    if not candidates:
        return None
    # Prefer the entry whose home/away orientation matches the Kalshi title.
    for g in candidates:
        if g.home == parsed.home and g.away == parsed.away:
            return g
    return candidates[0]


def fair_value_for_market(
    market: Market,
    games: list[GameOdds],
    *,
    now: datetime | None = None,
) -> FairValue | None:
    """Fair probability of the market's YES side, or None if it can't be matched."""
    parsed = parse_kalshi_game(market.title, market.yes_sub_title, market.ticker)
    if not parsed:
        return None
    game = _match_game(parsed, games)
    if not game:
        return None
    if parsed.yes_tricode == game.home:
        probs = game.home_book_probs
    elif parsed.yes_tricode == game.away:
        probs = game.away_book_probs
    else:
        return None
    if not probs:
        return None
    now = now or datetime.now(UTC)
    hours_to_tip: float | None = None
    if game.commence_time:
        tip = game.commence_time
        if tip.tzinfo is None:
            tip = tip.replace(tzinfo=UTC)
        hours_to_tip = max(0.0, (tip - now).total_seconds() / 3600.0)
    return fair_value(
        probs,
        kalshi_spread=market.spread,
        hours_to_tip=hours_to_tip,
        source=game.source,
    )
