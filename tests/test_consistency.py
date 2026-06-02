"""Consistency metrics + the live-trading gate."""

from __future__ import annotations

from kalshi_edge.backtest.consistency import (
    ConsistencyMetrics,
    SettledTrade,
    compute_metrics,
    evaluate_gate,
    synthetic_settled_trades,
)


def test_metrics_on_known_set() -> None:
    trades = [
        SettledTrade("A", "yes", 0.50, 10, 0.02, 0.60, 0.55, outcome_yes=1),  # YES bet, YES won
        SettledTrade("B", "no", 0.50, 10, 0.02, 0.40, 0.45, outcome_yes=0),  # NO bet, NO won
    ]
    m = compute_metrics(trades)
    assert m.n == 2
    assert m.hit_rate == 1.0
    # Each: payoff 10 - cost (5 + 0.02) = 4.98 -> total 9.96, cost 10.04.
    assert m.total_pnl == 9.96
    assert m.roi == round(9.96 / 10.04, 4)
    # Brier: model ((0.6-1)^2+(0.4-0)^2)/2 = 0.16; market ((0.55-1)^2+(0.45-0)^2)/2 = 0.2025.
    assert m.brier_model == 0.16
    assert m.brier_market == 0.2025
    assert m.model_beats_market


def test_gate_passes_and_fails() -> None:
    passing = ConsistencyMetrics(
        n=150,
        hit_rate=0.55,
        roi=0.05,
        total_pnl=100.0,
        total_cost=2000.0,
        brier_model=0.18,
        brier_market=0.21,
        model_beats_market=True,
        calibration_error=0.04,
        pnl_tstat=2.0,
    )
    assert evaluate_gate(passing).passed

    failing = ConsistencyMetrics(
        n=50,
        hit_rate=0.50,
        roi=-0.02,
        total_pnl=-10.0,
        total_cost=500.0,
        brier_model=0.25,
        brier_market=0.22,
        model_beats_market=False,
        calibration_error=0.20,
    )
    decision = evaluate_gate(failing)
    assert not decision.passed
    failed = [name for name, ok, _ in decision.checks if not ok]
    assert len(failed) >= 3  # trades, roi, brier, calibration all fail


def test_synthetic_generates_consistent_data() -> None:
    trades = synthetic_settled_trades(120, seed=7)
    assert len(trades) == 120
    m = compute_metrics(trades)
    assert m.n == 120
    assert 0.0 <= m.hit_rate <= 1.0
    assert m.calibration  # non-empty calibration curve
