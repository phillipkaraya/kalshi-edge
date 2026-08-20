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


def _snapshot_game(conn) -> None:
    """Record that this game has exactly two markets, as a live pass would."""
    for team in ("SAS", "NYK"):
        conn.execute(
            "INSERT INTO market_snapshots (ts, ticker, event_ticker) VALUES (?, ?, ?)",
            ("2026-06-10T00:00:00Z", f"KXNBAGAME-26JUN10SASNYK-{team}", "KXNBAGAME-26JUN10SASNYK"),
        )
    conn.commit()


def _engine(tmp_path, mode: str) -> ExecutionEngine:
    settings = Settings(execution_mode=mode)  # type: ignore[arg-type]
    conn = connect(tmp_path / "t.db")
    risk = RiskManager(RiskConfig.from_settings(settings), settings.bankroll)
    return ExecutionEngine(settings, conn, risk, kalshi_client=None)


def test_paper_fill_then_cap(tmp_path) -> None:
    engine = _engine(tmp_path, "paper")
    first = engine.submit(TICKET)
    assert first.status == "filled"
    # 96, not the requested 100: the market cap is $50 and 100 contracts would cost
    # $51.75 once the fee is paid, so the gate reserves the fee (HARDENING #5).
    assert first.contracts == 96
    # Second submit: already at the market cap -> rejected.
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
    _snapshot_game(engine.conn)
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


def test_demo_records_pending_not_filled(tmp_path) -> None:
    """Acceptance is not execution (HARDENING #1).

    Kalshi returning an order_id means the order was accepted, not that anyone traded
    with it. The row must land as `pending` with a zero fill so the consistency gate
    never grades a trade that did not happen.
    """
    engine = _engine_with(tmp_path, "demo", kalshi_env="demo", client=_FakeClient())
    res = engine.submit(TICKET)
    assert res.status == "pending"
    assert "accepted" in res.reason
    row = engine.conn.execute("SELECT status, count, filled_count FROM orders").fetchone()
    assert row["status"] == "pending"
    assert row["filled_count"] == 0
    assert row["count"] > 0  # the requested size is still recorded


def test_pending_order_still_consumes_cap_room(tmp_path) -> None:
    """A resting order can fill at any moment, so it must not be invisible to sizing."""
    engine = _engine_with(tmp_path, "demo", kalshi_env="demo", client=_FakeClient())
    first = engine.submit(TICKET)
    assert first.status == "pending"
    second = engine.submit(TICKET)  # same market, nothing has actually filled yet
    assert second.status == "rejected"
    assert "cap" in second.reason


def test_immediate_full_fill_is_recorded_as_filled(tmp_path) -> None:
    """When Kalshi reports the whole order filled on acceptance, trust it."""

    class _FillingClient:
        authenticated = True

        def create_order(self, **kwargs):
            return {"order": {"order_id": "x1", "status": "executed", "fill_count": "96.00"}}

    engine = _engine_with(tmp_path, "demo", kalshi_env="demo", client=_FillingClient())
    res = engine.submit(TICKET)
    assert res.status == "filled"
    row = engine.conn.execute("SELECT status, filled_count FROM orders").fetchone()
    assert (row["status"], row["filled_count"]) == ("filled", 96)


def test_hedge_earns_back_event_room(tmp_path) -> None:
    """Opposite sides of one game cannot both lose, so the cap must credit the hedge.

    Buying SAS-yes then NYK-yes in the same game is a hedge: exactly one pays out.
    Gross-spend accounting charged both against the per-game cap; worst-case netting
    recognises that the second leg adds little real risk (HARDENING #6).
    """
    engine = _engine(tmp_path, "paper")
    # The real pass snapshots every market in the game before trading it, and that is
    # what establishes the outcome universe (exactly one of SAS/NYK wins). Without it
    # the gate cannot prove the hedge is a hedge and stays conservative.
    _snapshot_game(engine.conn)
    first = engine.submit(TICKET)
    assert first.status == "filled"
    hedge = OrderTicket(
        **{**TICKET.__dict__, "ticker": "KXNBAGAME-26JUN10SASNYK-NYK", "side": "yes"}
    )
    second = engine.submit(hedge)
    assert second.status == "filled"
    # The hedge is not squeezed the way a correlated same-direction bet is: it clears
    # materially more than the handful of contracts the gross-spend cap would allow.
    assert second.contracts > first.contracts // 2


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


def test_submit_commit_persists_across_connections(tmp_path) -> None:
    # The BEGIN IMMEDIATE wrapper must actually COMMIT: a fresh connection to the same
    # db file has to see the filled order (not just the connection that wrote it).
    from kalshi_edge.execution.ledger import get_orders

    engine = _engine(tmp_path, "paper")
    assert engine.submit(TICKET).status == "filled"
    engine.conn.close()
    fresh = connect(tmp_path / "t.db")
    assert len(get_orders(fresh, mode="paper", status="filled")) == 1
    fresh.close()
