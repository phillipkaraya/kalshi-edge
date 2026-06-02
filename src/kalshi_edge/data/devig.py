"""Convert sportsbook odds to probabilities and remove the vig.

A book's two-sided prices imply probabilities that sum to MORE than 1.0 -- the
excess is the vig (overround). Removing it recovers the book's true probability
estimate. We aggregate the devigged probability across books (median by default)
to get a robust consensus that a single stale or off-market book can't distort.
"""

from __future__ import annotations

from statistics import median, pstdev


def decimal_to_prob(decimal_odds: float) -> float:
    """Implied (raw, vig-inclusive) probability from decimal odds."""
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {decimal_odds}")
    return 1.0 / decimal_odds


def american_to_prob(odds: int) -> float:
    """Implied (raw, vig-inclusive) probability from American odds."""
    if odds < 0:
        return -odds / (-odds + 100.0)
    return 100.0 / (odds + 100.0)


def overround(raw_probs: list[float]) -> float:
    """How much the raw probabilities exceed 1.0 (the book's vig)."""
    return sum(raw_probs) - 1.0


def devig_proportional(raw_probs: list[float]) -> list[float]:
    """Remove vig by normalising raw probabilities to sum to 1.0 (multiplicative)."""
    total = sum(raw_probs)
    if total <= 0.0:
        raise ValueError("raw probabilities must sum to a positive number")
    return [p / total for p in raw_probs]


def devig_two_way(home_raw: float, away_raw: float) -> tuple[float, float]:
    """Devig a two-outcome market, returning ``(p_home, p_away)`` summing to 1.0."""
    p_home, p_away = devig_proportional([home_raw, away_raw])
    return p_home, p_away


def consensus(probs: list[float], *, method: str = "median") -> float:
    """Aggregate devigged probabilities across books into a single estimate."""
    if not probs:
        raise ValueError("need at least one probability")
    if method == "mean":
        return sum(probs) / len(probs)
    return median(probs)


def dispersion(probs: list[float]) -> float:
    """Population standard deviation of the per-book probabilities (book disagreement)."""
    if len(probs) < 2:
        return 0.0
    return pstdev(probs)
