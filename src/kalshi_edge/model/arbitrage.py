"""Arbitrage / no-arbitrage checks on Kalshi quotes.

The cleanest case: one YES + one NO contract always pays exactly $1 at
settlement. If you can buy both for less than $1 (including fees), that's a
locked, risk-free profit. We also expose an event-level overround check for
mutually-exclusive multi-outcome events.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fees import DEFAULT_FEE_MULTIPLIER, order_fee


@dataclass(frozen=True)
class ArbResult:
    is_arb: bool
    profit_per_contract: float  # locked $ per YES+NO pair after fees (>0 means free money)
    detail: str


def two_sided_lock(
    yes_ask: float | None, no_ask: float | None, *, fee_mult: float = DEFAULT_FEE_MULTIPLIER
) -> ArbResult:
    """Buy 1 YES + 1 NO. Always redeems for $1, so cost+fees < $1 is a locked profit."""
    if yes_ask is None or no_ask is None:
        return ArbResult(False, 0.0, "missing quotes")
    fees = order_fee(1, yes_ask, multiplier=fee_mult) + order_fee(1, no_ask, multiplier=fee_mult)
    profit = round(1.0 - (yes_ask + no_ask) - fees, 4)
    return ArbResult(
        is_arb=profit > 0,
        profit_per_contract=profit,
        detail=f"YES {yes_ask} + NO {no_ask} + fees {round(fees, 4)} -> profit {profit}",
    )


def event_overround(yes_asks: list[float]) -> float:
    """Sum of YES asks across mutually-exclusive outcomes, minus 1.0.

    < 0 means the full set is buyable for under $1 (an under-round / lock candidate);
    > 0 is the usual house overround.
    """
    return round(sum(yes_asks) - 1.0, 4)
