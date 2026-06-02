"""Blend data sources into a fair probability and a confidence score.

v1 fair value IS the devigged sportsbook consensus. (Injury nudges and a stats
model join the blend in later slices.) Confidence is a tunable [0, 1] heuristic
combining book coverage, book agreement, Kalshi liquidity, and time-to-tip --
it later shrinks the edge before sizing so we bet less when we trust less.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data.devig import consensus, dispersion


@dataclass(frozen=True)
class FairValue:
    p_fair: float  # fair probability of YES
    confidence: float  # [0, 1]
    n_books: int
    dispersion: float
    source: str


def confidence_score(
    *,
    n_books: int,
    book_dispersion: float,
    kalshi_spread: float | None,
    hours_to_tip: float | None,
) -> float:
    """Combine signal-quality factors into a [0, 1] confidence. Weights are tunable."""
    coverage = min(n_books, 5) / 5.0
    agreement = max(0.0, 1.0 - book_dispersion / 0.10)
    liquidity = max(0.0, 1.0 - (kalshi_spread if kalshi_spread is not None else 0.10) / 0.10)
    # Closer to tip -> sharper, more reliable lines (>=48h out we treat as fully discounted).
    timing = 1.0 if hours_to_tip is None else min(1.0, 48.0 / max(hours_to_tip, 1.0))
    score = 0.35 * coverage + 0.30 * agreement + 0.20 * liquidity + 0.15 * timing
    return round(min(1.0, max(0.0, score)), 3)


def fair_value(
    book_probs: list[float],
    *,
    kalshi_spread: float | None = None,
    hours_to_tip: float | None = None,
    source: str = "consensus",
) -> FairValue:
    """Compute fair value for YES from per-book devigged probabilities."""
    p = consensus(book_probs)
    disp = dispersion(book_probs)
    conf = confidence_score(
        n_books=len(book_probs),
        book_dispersion=disp,
        kalshi_spread=kalshi_spread,
        hours_to_tip=hours_to_tip,
    )
    return FairValue(
        p_fair=p,
        confidence=conf,
        n_books=len(book_probs),
        dispersion=disp,
        source=source,
    )
