"""Go-live preflight: live must stay refused unless every condition is green."""

from __future__ import annotations

from kalshi_edge.backtest.consistency import ConsistencyMetrics
from kalshi_edge.config import Settings
from kalshi_edge.execution.live import live_preflight

PASS = ConsistencyMetrics(
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
FAIL = ConsistencyMetrics(
    n=10,
    hit_rate=0.40,
    roi=-0.10,
    total_pnl=-5.0,
    total_cost=50.0,
    brier_model=0.30,
    brier_market=0.25,
    model_beats_market=False,
    calibration_error=0.20,
)


def test_blocked_in_paper_mode() -> None:
    s = Settings(execution_mode="paper", live_enabled=True)
    assert not live_preflight(s, PASS, has_creds=True).go


def test_blocked_without_credentials() -> None:
    s = Settings(execution_mode="live", live_enabled=True)
    assert not live_preflight(s, PASS, has_creds=False).go


def test_blocked_when_gate_fails() -> None:
    s = Settings(execution_mode="live", live_enabled=True)
    assert not live_preflight(s, FAIL, has_creds=True).go


def test_blocked_when_kill_switch_on() -> None:
    s = Settings(execution_mode="live", live_enabled=True, kill_switch=True)
    assert not live_preflight(s, PASS, has_creds=True).go


def test_cleared_only_when_everything_green() -> None:
    s = Settings(execution_mode="live", live_enabled=True, kill_switch=False)
    result = live_preflight(s, PASS, has_creds=True)
    assert result.go
    assert all(c.ok for c in result.checks)
