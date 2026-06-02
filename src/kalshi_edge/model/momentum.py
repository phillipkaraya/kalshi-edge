"""Short-horizon microstructure signals from a market snapshot.

Two cheap, key-free signals straight off the Kalshi market payload:
  * order-book imbalance -- resting YES bid size vs ask size (buy/sell pressure)
  * line movement -- last price vs the previous price (where the market is drifting)
These act as confirmation filters around the fair-value edge, not standalone bets.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..kalshi.models import Market

_MOVE_EPS = 0.005  # ignore sub-half-cent drift as noise


@dataclass(frozen=True)
class MomentumSignal:
    imbalance: float | None  # (bid_size - ask_size)/(bid_size + ask_size), [-1, 1]
    price_move: float | None  # last - previous, in dollars
    label: str  # "up" | "down" | "flat"


def order_book_imbalance(bid_size: float | None, ask_size: float | None) -> float | None:
    """Resting-size imbalance on the YES book. +1 = all bids, -1 = all asks."""
    if bid_size is None or ask_size is None:
        return None
    total = bid_size + ask_size
    if total <= 0:
        return None
    return round((bid_size - ask_size) / total, 3)


def price_move(last: float | None, previous: float | None) -> float | None:
    if last is None or previous is None:
        return None
    return round(last - previous, 4)


def momentum_signal(market: Market) -> MomentumSignal:
    imbalance = order_book_imbalance(market.yes_bid_size, market.yes_ask_size)
    move = price_move(market.last_price, market.previous_price)
    label = "flat"
    if move is not None and move > _MOVE_EPS:
        label = "up"
    elif move is not None and move < -_MOVE_EPS:
        label = "down"
    return MomentumSignal(imbalance=imbalance, price_move=move, label=label)
