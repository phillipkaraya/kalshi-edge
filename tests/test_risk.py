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
    # Budget = 0.05 * 1000 = $50. Naive 50/0.50 = 100 contracts, but 100 contracts
    # ALSO owe a $1.75 fee -> $51.75, breaching the very cap being enforced. The gate
    # must reserve the fee: 96 costs $49.68, and 97 would cost $50.20.
    d = _rm().check(mode="paper", **OK)
    assert d.allowed
    assert d.max_contracts == 96


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
    # Budget = 0.5 * 1000 = $500, $499.50 used -> $0.50 left. One contract costs
    # $0.50 PLUS a $0.02 fee, so nothing fits and the order is refused outright.
    d = _rm().check(mode="paper", **{**OK, "current_exposure": 499.50})
    assert not d.allowed

    # With a dollar of room, one contract ($0.52 all-in) fits and two ($1.04) do not.
    d = _rm().check(mode="paper", **{**OK, "current_exposure": 499.0})
    assert d.allowed
    assert d.max_contracts == 1


def test_event_cap_limits_correlated_game_exposure() -> None:
    # Per-game budget = 0.06 * 1000 = $60, $58 committed -> $2 left. Four contracts
    # would cost $2.07 with fees (the pre-fee-reserve answer); three cost $1.56.
    d = _rm().check(mode="paper", **{**OK, "current_event_exposure": 58.0})
    assert d.allowed
    assert d.max_contracts == 3


def test_no_cap_is_ever_breached_once_fees_are_paid() -> None:
    """Property: the allowed size plus its own fee never exceeds the tightest cap."""
    from kalshi_edge.model.fees import order_fee

    for price in (0.03, 0.25, 0.50, 0.77, 0.97):
        for used in (0.0, 10.0, 49.0, 49.9):
            d = _rm().check(
                mode="paper",
                **{**OK, "price": price, "requested_contracts": 10_000, "current_exposure": used},
            )
            if not d.allowed:
                continue
            n = d.max_contracts
            spent = n * price + order_fee(n, price)
            assert spent <= 0.05 * 1000 + 1e-9, (price, used, n, spent)
            assert spent + used <= 0.5 * 1000 + 1e-9, (price, used, n, spent)


def test_kill_switch_file_halts_a_running_session(tmp_path) -> None:
    """The file is polled per order, so a kill lands mid-run rather than next restart."""
    kill = tmp_path / "KILL"
    rm = _rm(kill_switch_file=kill)
    assert rm.check(mode="paper", **OK).allowed  # nothing engaged yet
    kill.write_text("stop")
    d = rm.check(mode="paper", **OK)  # same manager instance, no reconstruction
    assert not d.allowed
    assert "kill switch" in d.reason
    kill.unlink()
    assert rm.check(mode="paper", **OK).allowed
