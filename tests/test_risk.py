"""Risk gate rules."""

from __future__ import annotations

from dataclasses import replace

from kalshi_edge.execution.risk import RiskConfig, RiskManager

CFG = RiskConfig(
    min_ev=0.01,
    min_liquidity_spread=0.05,
    max_contracts_per_market=200,
    max_position_fraction=0.05,
    max_total_exposure_fraction=0.5,
    max_daily_loss=100.0,
    kill_switch=False,
    live_enabled=False,
)

OK = dict(
    ev_net=0.05,
    spread=0.02,
    requested_contracts=100,
    price=0.50,
    current_position_contracts=0,
    current_exposure=0.0,
    daily_pnl=0.0,
)


def _rm(**over) -> RiskManager:
    return RiskManager(replace(CFG, **over), bankroll=1000.0)


def test_allows_and_clamps_to_market_cap() -> None:
    # 0.05 * 1000 / 0.50 = 100 contract cap.
    d = _rm().check(mode="paper", **OK)
    assert d.allowed
    assert d.max_contracts == 100


def test_kill_switch_blocks_everything() -> None:
    assert not _rm(kill_switch=True).check(mode="paper", **OK).allowed


def test_live_locked_until_enabled() -> None:
    assert not _rm().check(mode="live", **OK).allowed
    # Now requires BOTH the LIVE_ENABLED flag AND a passed consistency gate.
    assert not _rm(live_enabled=True).check(mode="live", **OK).allowed
    assert _rm(live_enabled=True).check(mode="live", **{**OK, "live_gate_passed": True}).allowed


def test_min_ev_and_spread_and_daily_loss() -> None:
    assert not _rm().check(mode="paper", **{**OK, "ev_net": 0.005}).allowed
    assert not _rm().check(mode="paper", **{**OK, "spread": 0.10}).allowed
    assert not _rm().check(mode="paper", **{**OK, "daily_pnl": -100.0}).allowed


def test_existing_position_consumes_market_room() -> None:
    d = _rm().check(mode="paper", **{**OK, "current_position_contracts": 100})
    assert not d.allowed  # already at the 100-contract market cap


def test_exposure_cap_limits_size() -> None:
    # Exposure budget = 0.5 * 1000 = 500; already 499.50 used -> room for 1 @ 0.50.
    d = _rm().check(mode="paper", **{**OK, "current_exposure": 499.50})
    assert d.allowed
    assert d.max_contracts == 1


def test_event_cap_limits_correlated_game_exposure() -> None:
    # Per-game budget = 0.06 * 1000 = $60; $58 already on this game -> room for 4 @ $0.50.
    d = _rm().check(mode="paper", **{**OK, "current_event_exposure": 58.0})
    assert d.allowed
    assert d.max_contracts == 4
