"""Ingest settled Kalshi results and join them with paper orders for grading."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from ..kalshi.client import KalshiClient
from .consistency import SettledTrade


def record_settlement(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    event_ticker: str | None,
    result: str,
    last_price: float | None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO settlements (ticker, event_ticker, result, settled_ts, last_price)
           VALUES (?, ?, ?, ?, ?)""",
        (ticker, event_ticker, result, datetime.now(UTC).isoformat(), last_price),
    )
    conn.commit()


def ingest_settlements(
    conn: sqlite3.Connection, client: KalshiClient, series_tickers: list[str]
) -> int:
    """Fetch settled markets for the given series and store their YES/NO results."""
    recorded = 0
    for series in series_tickers:
        page = client.get_markets(series_ticker=series, status="settled", limit=200)
        for m in page.markets:
            if m.result in ("yes", "no"):
                record_settlement(
                    conn,
                    ticker=m.ticker,
                    event_ticker=m.event_ticker,
                    result=m.result,
                    last_price=m.last_price,
                )
                recorded += 1
    return recorded


def build_settled_trades(conn: sqlite3.Connection, *, mode: str) -> list[SettledTrade]:
    """Join filled orders with recorded settlements into gradeable trades."""
    rows = conn.execute(
        """SELECT o.ticker, o.side, o.price, o.count, o.fee, o.p_fair, o.p_market, s.result
           FROM orders o JOIN settlements s ON o.ticker = s.ticker
           WHERE o.mode = ? AND o.status = 'filled'
             AND o.p_fair IS NOT NULL AND o.p_market IS NOT NULL""",
        (mode,),
    ).fetchall()
    return [
        SettledTrade(
            ticker=r["ticker"],
            side=r["side"],
            price=r["price"],
            count=r["count"],
            fee=r["fee"],
            p_fair=r["p_fair"],
            p_market=r["p_market"],
            outcome_yes=1 if r["result"] == "yes" else 0,
        )
        for r in rows
    ]
