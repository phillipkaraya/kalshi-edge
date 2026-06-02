"""Turn a fair probability + Kalshi quotes into an actionable edge.

For a binary contract that pays $1 if it resolves YES:
  * Buy YES at ``yes_ask``: you win (1 - yes_ask) with prob p_fair, lose yes_ask otherwise.
    EV per contract = p_fair - yes_ask   (gross, before fees).
  * Buy NO at ``no_ask``: symmetric, wins with prob (1 - p_fair).
We evaluate both sides, subtract the per-contract fee, pick the better one if it
clears ``min_ev``, then size it with fractional Kelly under a hard bankroll cap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .fees import DEFAULT_FEE_MULTIPLIER, order_fee

_NEG_INF = float("-inf")
_EPS = 1e-9  # absorbs float drift so floor() doesn't shed a contract at an integer boundary


@dataclass(frozen=True)
class EdgeResult:
    side: str  # "yes" | "no" | "none"
    price: float | None  # ask paid for the chosen side
    p_fair: float  # fair probability of YES
    p_win: float | None  # fair probability the CHOSEN side wins
    edge: float | None  # p_win - price (gross)
    ev_net: float | None  # edge - per-contract fee
    fee: float | None
    kelly_fraction: float  # fraction of bankroll (after the fractional-Kelly multiplier + cap)
    suggested_contracts: int


def kelly_fraction(p_win: float, price: float) -> float:
    """Full-Kelly bankroll fraction for a $1-payoff contract bought at ``price``.

    f* = (p_win - price) / (1 - price). Clamped to >= 0 (no edge -> no bet).
    """
    if price >= 1.0 or price <= 0.0:
        return 0.0
    return max(0.0, (p_win - price) / (1.0 - price))


def _side_ev(p_win: float, ask: float | None, fee_mult: float) -> tuple[float, float | None]:
    """Return (ev_net, fee) for buying a side at ``ask`` whose win prob is ``p_win``."""
    if ask is None or ask <= 0.0:
        return _NEG_INF, None
    # Use the realized, cent-rounded per-contract fee (conservative) so thin edges
    # the smooth fee would falsely rank +EV are correctly rejected.
    fee = order_fee(1, ask, multiplier=fee_mult)
    return (p_win - ask - fee), fee


def evaluate_edge(
    p_fair: float,
    yes_ask: float | None,
    no_ask: float | None,
    *,
    bankroll: float,
    kelly_frac: float = 0.25,
    max_fraction: float = 0.05,
    min_ev: float = 0.0,
    fee_mult: float = DEFAULT_FEE_MULTIPLIER,
) -> EdgeResult:
    ev_yes, fee_yes = _side_ev(p_fair, yes_ask, fee_mult)
    ev_no, fee_no = _side_ev(1.0 - p_fair, no_ask, fee_mult)

    if max(ev_yes, ev_no) < min_ev:
        return EdgeResult("none", None, p_fair, None, None, None, None, 0.0, 0)

    if ev_yes >= ev_no:
        side, price, p_win, ev_net, fee = "yes", yes_ask, p_fair, ev_yes, fee_yes
    else:
        side, price, p_win, ev_net, fee = "no", no_ask, 1.0 - p_fair, ev_no, fee_no

    assert price is not None  # guaranteed: the winning side cleared min_ev, so its ask existed
    f_full = kelly_fraction(p_win, price)
    frac = min(kelly_frac * f_full, max_fraction)
    contracts = math.floor(frac * bankroll / price + _EPS) if price > 0 else 0
    return EdgeResult(
        side=side,
        price=price,
        p_fair=p_fair,
        p_win=p_win,
        edge=p_win - price,
        ev_net=ev_net,
        fee=fee,
        kelly_fraction=round(frac, 4),
        suggested_contracts=contracts,
    )
