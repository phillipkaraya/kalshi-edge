"""Kalshi trading fees.

Per Kalshi's fee schedule (last updated Feb 2026), the taker fee is parabolic:

    fee = multiplier * contracts * P * (1 - P)        # P = price in dollars (0-1)

rounded UP to the next cent at the order level. The multiplier is 0.07 for
standard categories including sports (higher for premium markets like crypto).
Maker fees are 25% of the taker fee.

We expose two views:
  * ``taker_fee_per_contract`` -- the smooth, un-rounded marginal fee, used for
    per-contract EV ranking (rounding is a small order-level effect).
  * ``order_fee`` -- the rounded-up total an order actually pays, used by the
    ledger / execution layer.
"""

from __future__ import annotations

import math

DEFAULT_FEE_MULTIPLIER = 0.07
MAKER_FRACTION = 0.25


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def taker_fee_per_contract(price: float, multiplier: float = DEFAULT_FEE_MULTIPLIER) -> float:
    """Smooth (un-rounded) taker fee for ONE contract bought at ``price`` dollars."""
    p = _clamp01(price)
    return multiplier * p * (1.0 - p)


def order_fee(
    contracts: int,
    price: float,
    *,
    maker: bool = False,
    multiplier: float = DEFAULT_FEE_MULTIPLIER,
) -> float:
    """Total fee for ``contracts`` at ``price``, rounded UP to the next cent.

    Kalshi rounds fees up, so even tiny orders pay at least 1c. Maker orders pay
    25% of the taker fee.
    """
    p = _clamp01(price)
    raw = multiplier * contracts * p * (1.0 - p)
    if maker:
        raw *= MAKER_FRACTION
    # Quantize away float noise (~1e-13) before rounding up, so an exact $1.75
    # doesn't drift to $1.76 -- while a genuine fraction of a cent still rounds up.
    cents = math.ceil(round(raw * 100.0, 6))
    return cents / 100.0


def max_contracts_within_budget(
    budget: float,
    price: float,
    *,
    maker: bool = False,
    multiplier: float = DEFAULT_FEE_MULTIPLIER,
) -> int:
    """Largest ``n`` with ``n*price + order_fee(n, price) <= budget``.

    Cap math used to divide the remaining budget by price alone, reserving nothing
    for the fee the order itself will incur -- so a position could be sized right up
    to a cap and then breach it the moment the fee landed.

    ``order_fee`` rounds up to the next cent, so there is no exact closed form. Seed
    from the smooth per-contract fee (which ignores only the sub-cent rounding, so it
    lands within a contract or two) and settle it with bounded steps. Both loops are
    capped because this sits on the order path.
    """
    if budget <= 0.0 or not (0.0 < price < 1.0):
        return 0

    def total(n: int) -> float:
        return n * price + order_fee(n, price, maker=maker, multiplier=multiplier)

    per = price + taker_fee_per_contract(price, multiplier) * (MAKER_FRACTION if maker else 1.0)
    n = max(0, int(budget // per))
    for _ in range(8):  # seed can sit a contract or two low; step up while it still fits
        if total(n + 1) > budget:
            break
        n += 1
    for _ in range(8):  # ...and back off if the rounded fee pushed it over
        if n <= 0 or total(n) <= budget:
            break
        n -= 1
    return n if n > 0 and total(n) <= budget else 0
