"""Execution engine: paper fills, caps, and locked live/demo modes."""

from __future__ import annotations

from kalshi_edge.config import Settings
from kalshi_edge.execution.engine import ExecutionEngine, OrderTicket
from kalshi_edge.execution.risk import RiskConfig, RiskManager
from kalshi_edge.storage import connect

TICKET = OrderTicket(
    ticker="KXNBAGAME-26JUN10SASNYK-SAS",
    event_ticker="KXNBAGAME-26JUN10SASNYK",
    side="yes",
    price=0.50,
    count=100,
    p_fair=0.60,
    ev_net=0.05,
    spread=0.02,
)


def _engine(tmp_path, mode: str) -> ExecutionEngine:
    settings = Settings(execution_mode=mode)  # type: ignore[arg-type]
    conn = connect(tmp_path / "t.db")
    risk = RiskManager(RiskConfig.from_settings(settings), settings.bankroll)
    return ExecutionEngine(settings, conn, risk, kalshi_client=None)


def test_paper_fill_then_cap(tmp_path) -> None:
    engine = _engine(tmp_path, "paper")
    first = engine.submit(TICKET)
    assert first.status == "filled"
    assert first.contracts == 100
    # Second submit: already at the 100-contract market cap -> rejected.
    second = engine.submit(TICKET)
    assert second.status == "rejected"
    assert "cap" in second.reason


def test_live_is_locked(tmp_path) -> None:
    engine = _engine(tmp_path, "live")
    res = engine.submit(TICKET)
    assert res.status == "rejected"
    assert "live" in res.reason.lower()


def test_demo_without_creds_rejected(tmp_path) -> None:
    engine = _engine(tmp_path, "demo")
    res = engine.submit(TICKET)
    assert res.status == "rejected"
    assert "creds" in res.reason


def test_thin_spread_blocks_fill(tmp_path) -> None:
    engine = _engine(tmp_path, "paper")
    wide = OrderTicket(**{**TICKET.__dict__, "spread": 0.20})
    res = engine.submit(wide)
    assert res.status == "rejected"
    assert "spread" in res.reason


def test_event_cap_limits_second_correlated_bet(tmp_path) -> None:
    engine = _engine(tmp_path, "paper")
    first = engine.submit(TICKET)
    assert first.status == "filled"
    # A different market on the SAME game -> capped by the per-event budget, not full size.
    other = OrderTicket(
        **{**TICKET.__dict__, "ticker": "KXNBAGAME-26JUN10SASNYK-NYK", "side": "no"}
    )
    second = engine.submit(other)
    assert second.status == "filled"
    assert 0 < second.contracts < first.contracts


class _FakeClient:
    authenticated = True

    def create_order(self, **kwargs):
        return {"order": {"order_id": "fake-123"}}


def _engine_with(tmp_path, mode, *, kalshi_env="prod", live_enabled=False, client=None):
    settings = Settings(execution_mode=mode, kalshi_env=kalshi_env, live_enabled=live_enabled)  # type: ignore[arg-type]
    conn = connect(tmp_path / "t.db")
    risk = RiskManager(RiskConfig.from_settings(settings), settings.bankroll)
    return ExecutionEngine(settings, conn, risk, client)


def test_live_refused_unless_gate_passes(tmp_path) -> None:
    # LIVE_ENABLED True + creds present, but no settled paper trades -> gate fails -> refused.
    # This proves the ENGINE enforces the consistency gate, not just the env flag.
    engine = _engine_with(tmp_path, "live", live_enabled=True, client=_FakeClient())
    res = engine.submit(TICKET)
    assert res.status == "rejected"
    assert "gate" in res.reason.lower()


def test_demo_requires_demo_env(tmp_path) -> None:
    # Prod host + "demo" mode would send a real order -> must be refused.
    engine = _engine_with(tmp_path, "demo", kalshi_env="prod", client=_FakeClient())
    res = engine.submit(TICKET)
    assert res.status == "rejected"
    assert "KALSHI_ENV=demo" in res.reason


def test_demo_places_on_demo_env(tmp_path) -> None:
    engine = _engine_with(tmp_path, "demo", kalshi_env="demo", client=_FakeClient())
    res = engine.submit(TICKET)
    assert res.status == "filled"
    assert "demo order placed" in res.reason


def test_daily_loss_cap_blocks_after_settled_loss(tmp_path) -> None:
    from kalshi_edge.backtest.settlement import record_settlement
    from kalshi_edge.execution.ledger import OrderRecord, record_order

    settings = Settings(execution_mode="paper")  # type: ignore[arg-type]
    conn = connect(tmp_path / "t.db")
    # A filled paper buy that LOST today: 300 @ $0.50 -> -$150 realized (< the $100 cap).
    record_order(
        conn,
        OrderRecord(
            mode="paper",
            ticker="KX-G1-A",
            event_ticker="KX-G1",
            side="yes",
            action="buy",
            count=300,
            price=0.50,
            fee=0.0,
            status="filled",
        ),
    )
    record_settlement(conn, ticker="KX-G1-A", event_ticker="KX-G1", result="no", last_price=0.0)
    risk = RiskManager(RiskConfig.from_settings(settings), settings.bankroll)
    engine = ExecutionEngine(settings, conn, risk, None)
    res = engine.submit(TICKET)  # daily_pnl auto-computed from the ledger
    assert res.status == "rejected"
    assert "daily loss" in res.reason
