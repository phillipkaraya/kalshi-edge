"""Fractional-Kelly sizing."""

from __future__ import annotations

import pytest

from kalshi_edge.model.edge import kelly_fraction


def test_kelly_positive_edge() -> None:
    # p=0.60 at price 0.50 -> f* = (0.60-0.50)/(1-0.50) = 0.20
    assert kelly_fraction(0.60, 0.50) == pytest.approx(0.20)


def test_kelly_no_edge_is_zero() -> None:
    assert kelly_fraction(0.50, 0.50) == 0.0


def test_kelly_negative_edge_clamped_to_zero() -> None:
    assert kelly_fraction(0.40, 0.50) == 0.0


def test_kelly_degenerate_prices() -> None:
    assert kelly_fraction(0.99, 1.0) == 0.0
    assert kelly_fraction(0.50, 0.0) == 0.0
