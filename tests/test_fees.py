"""Kalshi fee formula, checked against the published Feb 2026 schedule."""

from __future__ import annotations

import pytest

from kalshi_edge.model.fees import order_fee, taker_fee_per_contract


def test_per_contract_peaks_at_fifty_cents() -> None:
    # 0.07 * 0.5 * 0.5 = 0.0175 -> the documented max per-contract fee.
    assert taker_fee_per_contract(0.50) == pytest.approx(0.0175)
    # Parabolic: cheaper toward the wings.
    assert taker_fee_per_contract(0.10) == pytest.approx(0.0063)
    assert taker_fee_per_contract(0.90) == pytest.approx(0.0063)


def test_order_fee_rounds_up_to_the_cent() -> None:
    # 100 contracts at 0.50: 0.07 * 100 * 0.25 = $1.75.
    assert order_fee(100, 0.50) == pytest.approx(1.75)
    # A single contract still pays at least a rounded-up cent (0.0175 -> 0.02).
    assert order_fee(1, 0.50) == pytest.approx(0.02)


def test_maker_fee_is_a_quarter_of_taker() -> None:
    # 100 contracts at 0.50 taker = $1.75; maker = 25% = $0.4375 -> rounds up to $0.44.
    assert order_fee(100, 0.50, maker=True) == pytest.approx(0.44)
