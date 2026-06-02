"""SQLite order ledger + position aggregation (shares the store from ``storage``)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class OrderRecord:
    mode: str
    ticker: str
    event_ticker: str | None
    side: str  # yes | no
    action: str  # buy | sell
    count: int
    price: float
    fee: float
    status: str  # filled | rejected | pending
    reason: str | None = None
    p_fair: float | None = None
    p_market: float | None = None
    ev_net: float | None = None
    kalshi_order_id: str | None = None


@dataclass(frozen=True)
class Position:
    ticker: str
    side: str
    contracts: int
    cost: float  # total $ committed including fees
    avg_price: float


def record_order(conn: sqlite3.Connection, rec: OrderRecord, *, commit: bool = True) -> int:
    cur = conn.execute(
        """INSERT INTO orders
           (ts, mode, ticker, event_ticker, side, action, count, price, fee,
            status, reason, p_fair, p_market, ev_net, kalshi_order_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(UTC).isoformat(),
            rec.mode,
            rec.ticker,
            rec.event_ticker,
            rec.side,
            rec.action,
            rec.count,
            rec.price,
            rec.fee,
            rec.status,
            rec.reason,
            rec.p_fair,
            rec.p_market,
            rec.ev_net,
            rec.kalshi_order_id,
        ),
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid or 0)


def get_orders(
    conn: sqlite3.Connection, *, mode: str | None = None, status: str | None = None
) -> list[sqlite3.Row]:
    query = "SELECT * FROM orders WHERE 1=1"
    params: list[object] = []
    if mode:
        query += " AND mode = ?"
        params.append(mode)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY ts DESC, id DESC"
    return conn.execute(query, params).fetchall()


_NET = "SUM(CASE WHEN action='buy' THEN count ELSE -count END)"
_COST = "SUM(CASE WHEN action='buy' THEN count*price + fee ELSE -(count*price) END)"


def get_positions(conn: sqlite3.Connection, *, mode: str) -> list[Position]:
    rows = conn.execute(
        f"""SELECT ticker, side, {_NET} AS contracts, {_COST} AS cost
            FROM orders WHERE mode = ? AND status = 'filled'
            GROUP BY ticker, side HAVING {_NET} > 0""",
        (mode,),
    ).fetchall()
    out: list[Position] = []
    for r in rows:
        contracts = int(r["contracts"])
        cost = float(r["cost"])
        out.append(
            Position(
                ticker=r["ticker"],
                side=r["side"],
                contracts=contracts,
                cost=round(cost, 4),
                avg_price=round(cost / contracts, 4) if contracts else 0.0,
            )
        )
    return out


def position_contracts(conn: sqlite3.Connection, *, mode: str, ticker: str, side: str) -> int:
    row = conn.execute(
        f"SELECT COALESCE({_NET}, 0) AS c FROM orders "
        "WHERE mode = ? AND status = 'filled' AND ticker = ? AND side = ?",
        (mode, ticker, side),
    ).fetchone()
    return int(row["c"])


def total_exposure(conn: sqlite3.Connection, *, mode: str) -> float:
    return round(sum(p.cost for p in get_positions(conn, mode=mode)), 4)


def event_exposure(conn: sqlite3.Connection, *, mode: str, event_ticker: str | None) -> float:
    """Total $ committed across all filled buys for one game (event_ticker)."""
    if not event_ticker:
        return 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(count * price + fee), 0.0) AS c FROM orders "
        "WHERE mode = ? AND status = 'filled' AND action = 'buy' AND event_ticker = ?",
        (mode, event_ticker),
    ).fetchone()
    return round(float(row["c"]), 4)
