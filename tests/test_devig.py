"""Odds conversion and vig removal."""

from __future__ import annotations

import pytest

from kalshi_edge.data.devig import (
    american_to_prob,
    consensus,
    decimal_to_prob,
    devig_two_way,
    dispersion,
    overround,
)


def test_decimal_to_prob() -> None:
    assert decimal_to_prob(2.0) == pytest.approx(0.5)
    assert decimal_to_prob(1.5) == pytest.approx(0.6667, abs=1e-4)
    with pytest.raises(ValueError):
        decimal_to_prob(1.0)


def test_american_to_prob() -> None:
    assert american_to_prob(-110) == pytest.approx(0.5238, abs=1e-4)
    assert american_to_prob(+150) == pytest.approx(0.40)


def test_invalid_inputs_raise() -> None:
    with pytest.raises(ValueError):
        american_to_prob(0)
    with pytest.raises(ValueError):
        devig_two_way(0.5, 0.0)  # a zero raw probability is invalid


def test_devig_two_way_sums_to_one() -> None:
    home_raw = decimal_to_prob(1.5)  # 0.6667
    away_raw = decimal_to_prob(2.5)  # 0.4000
    assert overround([home_raw, away_raw]) == pytest.approx(0.0667, abs=1e-3)
    p_home, p_away = devig_two_way(home_raw, away_raw)
    assert p_home + p_away == pytest.approx(1.0)
    assert p_home == pytest.approx(0.625, abs=1e-3)
    assert p_away == pytest.approx(0.375, abs=1e-3)


def test_consensus_and_dispersion() -> None:
    assert consensus([0.60, 0.62, 0.64]) == pytest.approx(0.62)  # median
    assert consensus([0.60, 0.62, 0.64], method="mean") == pytest.approx(0.62)
    assert dispersion([0.60]) == 0.0
    assert dispersion([0.60, 0.64]) > 0.0
