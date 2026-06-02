"""SQLite logging for market snapshots and computed signals."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .kalshi.models import Market
from .model.edge import EdgeResult
from .model.probability import FairValue

SCHEMA_PATH = Path(__file__).parent / "db" / "schema.sql"
_ALLOWED_TABLES = {"market_snapshots", "signals"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open (creating dirs + schema as needed) a connection to the local store."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # Hardening: WAL + a busy timeout reduce read/write contention between passes.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn


def log_snapshot(conn: sqlite3.Connection, market: Market) -> None:
    conn.execute(
        """INSERT INTO market_snapshots
           (ts, ticker, event_ticker, yes_bid, yes_ask, no_bid, no_ask,
            last_price, volume, open_interest, spread)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _now(),
            market.ticker,
            market.event_ticker,
            market.yes_bid,
            market.yes_ask,
            market.no_bid,
            market.no_ask,
            market.last_price,
            market.volume,
            market.open_interest,
            market.spread,
        ),
    )
    conn.commit()


def log_signal(
    conn: sqlite3.Connection,
    ticker: str,
    p_market: float | None,
    fair: FairValue,
    edge: EdgeResult,
) -> None:
    conn.execute(
        """INSERT INTO signals
           (ts, ticker, p_market, p_fair, confidence, n_books, side, price,
            edge, ev_net, fee, kelly_fraction, suggested_contracts, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _now(),
            ticker,
            p_market,
            fair.p_fair,
            fair.confidence,
            fair.n_books,
            edge.side,
            edge.price,
            edge.edge,
            edge.ev_net,
            edge.fee,
            edge.kelly_fraction,
            edge.suggested_contracts,
            fair.source,
        ),
    )
    conn.commit()


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"unknown table: {table}")
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
