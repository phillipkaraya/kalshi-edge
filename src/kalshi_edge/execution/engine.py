"""Execution engine: turns an edge into a (paper/demo/live) order via the risk gate.

paper -> simulated fill at the quoted ask, logged to the ledger (no creds, no money).
demo  -> real order against Kalshi's sandbox (needs creds).
live  -> real money; refused unless the consistency gate passes AND LIVE_ENABLED is set.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ..backtest.consistency import compute_metrics, evaluate_gate
from ..backtest.settlement import build_settled_trades, realized_daily_pnl
from ..config import Settings
from ..kalshi.client import KalshiClient
from ..kalshi.models import Market
from ..model.edge import EdgeResult
from ..model.fees import order_fee
from .ledger import (
    OrderRecord,
    event_exposure,
    position_contracts,
    record_order,
    total_exposure,
)
from .risk import RiskManager


@dataclass(frozen=True)
class OrderTicket:
    ticker: str
    event_ticker: str | None
    side: str  # yes | no
    price: float
    count: int
    p_fair: float | None = None
    p_market: float | None = None
    ev_net: float | None = None
    spread: float | None = None


@dataclass(frozen=True)
class ExecutionResult:
    status: str  # filled | rejected
    contracts: int
    reason: str
    order_id: int | None = None


def ticket_from_edge(market: Market, edge: EdgeResult) -> OrderTicket | None:
    """Build an order ticket from an actionable edge, or None if there's nothing to do."""
    if edge.side not in ("yes", "no") or edge.price is None or edge.suggested_contracts <= 0:
        return None
    return OrderTicket(
        ticker=market.ticker,
        event_ticker=market.event_ticker,
        side=edge.side,
        price=edge.price,
        count=edge.suggested_contracts,
        p_fair=edge.p_fair,
        p_market=market.implied_prob,
        ev_net=edge.ev_net,
        spread=market.spread,
    )


class ExecutionEngine:
    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        risk: RiskManager,
        kalshi_client: KalshiClient | None = None,
    ) -> None:
        self.settings = settings
        self.conn = conn
        self.risk = risk
        self.client = kalshi_client
        self.mode = settings.execution_mode

    def submit(self, ticket: OrderTicket, *, daily_pnl: float | None = None) -> ExecutionResult:
        held = position_contracts(self.conn, mode=self.mode, ticker=ticket.ticker, side=ticket.side)
        exposure = total_exposure(self.conn, mode=self.mode)
        event_exp = event_exposure(self.conn, mode=self.mode, event_ticker=ticket.event_ticker)
        # Realized daily PnL from settled trades drives the daily-loss cap (was inert).
        if daily_pnl is None:
            daily_pnl = realized_daily_pnl(
                self.conn, mode=self.mode, on_date=datetime.now(UTC).date().isoformat()
            )
        # Live orders must clear the consistency gate computed from real settled paper
        # trades -- enforced HERE on the order path, not just on the Go-Live UI page.
        gate_passed = False
        if self.mode == "live":
            metrics = compute_metrics(build_settled_trades(self.conn, mode="paper"))
            gate_passed = evaluate_gate(metrics).passed
        decision = self.risk.check(
            mode=self.mode,
            ev_net=ticket.ev_net,
            spread=ticket.spread,
            requested_contracts=ticket.count,
            price=ticket.price,
            current_position_contracts=held,
            current_exposure=exposure,
            current_event_exposure=event_exp,
            daily_pnl=daily_pnl,
            live_gate_passed=gate_passed,
        )
        if not decision.allowed:
            self._record(ticket, 0, 0.0, "rejected", decision.reason, None)
            return ExecutionResult("rejected", 0, decision.reason)

        n = decision.max_contracts
        fee = order_fee(n, ticket.price, multiplier=self.settings.fee_multiplier)

        if self.mode == "paper":
            oid = self._record(ticket, n, fee, "filled", "paper fill", None)
            return ExecutionResult("filled", n, "paper fill", oid)

        # demo + live place real orders. live only reaches here after the gate passed
        # in risk.check (above). Guard the host so "demo" can't hit prod and "live"
        # can't silently no-op against the sandbox.
        if self.client is None or not self.client.authenticated:
            self._record(ticket, 0, 0.0, "rejected", f"{self.mode} needs Kalshi creds", None)
            return ExecutionResult("rejected", 0, f"{self.mode} needs Kalshi creds")
        want_env = "demo" if self.mode == "demo" else "prod"
        if self.settings.kalshi_env != want_env:
            reason = f"{self.mode} requires KALSHI_ENV={want_env}"
            self._record(ticket, 0, 0.0, "rejected", reason, None)
            return ExecutionResult("rejected", 0, reason)
        resp = self.client.create_order(
            ticker=ticket.ticker,
            action="buy",
            side=ticket.side,
            count=n,
            type_="limit",
            price_cents=round(ticket.price * 100),
        )
        order = resp.get("order", resp) if isinstance(resp, dict) else {}
        oid = self._record(
            ticket, n, fee, "filled", f"{self.mode} order placed", str(order.get("order_id", ""))
        )
        return ExecutionResult("filled", n, f"{self.mode} order placed", oid)

    def _record(
        self,
        ticket: OrderTicket,
        count: int,
        fee: float,
        status: str,
        reason: str,
        kalshi_order_id: str | None,
    ) -> int:
        return record_order(
            self.conn,
            OrderRecord(
                mode=self.mode,
                ticker=ticket.ticker,
                event_ticker=ticket.event_ticker,
                side=ticket.side,
                action="buy",
                count=count,
                price=ticket.price,
                fee=fee,
                status=status,
                reason=reason,
                p_fair=ticket.p_fair,
                p_market=ticket.p_market,
                ev_net=ticket.ev_net,
                kalshi_order_id=kalshi_order_id,
            ),
        )
