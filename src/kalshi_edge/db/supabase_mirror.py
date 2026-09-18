"""One-way mirror of the local SQLite ledger into Supabase.

SQLite stays the primary store and the trading engine never reads from here. This
exists only so a hosted board has durable data: both Streamlit Cloud and Vercel run
ephemeral filesystems, so ``data/kalshi_edge.db`` is invisible to them and every
hosted page rendered empty.

Two rules this module exists to honour:

* **It fails open.** A mirror problem must never break a trading pass. Missing
  config, a dead network, a PostgREST error: all of it is caught, counted and
  reported, never raised. The pass that computes edges is more important than the
  page that displays them.
* **It does not recompute anything.** ``kalshi_metrics`` is the serialized output of
  ``compute_metrics`` + ``evaluate_gate``, so the board renders numbers it never
  derives. The Brier, calibration and gate math has exactly one implementation.

Writes go to PostgREST with the service role key. Those tables have RLS enabled and
no policies, so nothing but the service role can read or write them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

import httpx

from ..backtest.consistency import compute_metrics, evaluate_gate
from ..backtest.settlement import build_settled_trades
from ..config import Settings

# PostgREST rejects very large bodies and Supabase caps statement time; 500 rows per
# request keeps a first-run backfill of a few thousand rows well inside both.
_CHUNK = 500
_TIMEOUT = httpx.Timeout(30.0)

# table -> (sqlite table, conflict columns, timestamp column for the watermark)
_TABLES: dict[str, tuple[str, str, str | None]] = {
    "kalshi_market_snapshots": ("market_snapshots", "ticker,ts", "ts"),
    "kalshi_signals": ("signals", "ticker,ts", "ts"),
    "kalshi_orders": ("orders", "ts,ticker,mode", "ts"),
    # Settlements are few and mutable (a market can resolve late), so re-upsert the
    # whole set rather than tracking a watermark.
    "kalshi_settlements": ("settlements", "ticker", None),
}


def _headers(settings: Settings) -> dict[str, str]:
    key = settings.supabase_service_role_key or ""
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _rows(
    conn: sqlite3.Connection, table: str, since: str | None, ts_col: str | None
) -> list[dict]:
    conn.row_factory = sqlite3.Row
    if ts_col and since:
        cur = conn.execute(f"select * from {table} where {ts_col} > ? order by {ts_col}", (since,))  # noqa: S608
    else:
        cur = conn.execute(f"select * from {table}")  # noqa: S608
    out = []
    for r in cur.fetchall():
        d = dict(r)
        # `id` is a local autoincrement; Postgres owns its own identity column and a
        # supplied value would collide with the sequence.
        d.pop("id", None)
        out.append(d)
    return out


def _watermark(client: httpx.Client, base: str, table: str, ts_col: str) -> str | None:
    """Latest mirrored timestamp, so a normal pass sends only new rows."""
    r = client.get(
        f"{base}/rest/v1/{table}",
        params={"select": ts_col, "order": f"{ts_col}.desc", "limit": 1},
    )
    r.raise_for_status()
    data = r.json()
    return data[0][ts_col] if data else None


def _upsert(client: httpx.Client, base: str, table: str, rows: list[dict], conflict: str) -> int:
    sent = 0
    for i in range(0, len(rows), _CHUNK):
        chunk = rows[i : i + _CHUNK]
        r = client.post(
            f"{base}/rest/v1/{table}",
            params={"on_conflict": conflict},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=chunk,
        )
        r.raise_for_status()
        sent += len(chunk)
    return sent


def _metrics_row(conn: sqlite3.Connection, mode: str = "paper") -> dict[str, Any]:
    """Serialize the existing grading engine. No math is reimplemented here."""
    m = compute_metrics(build_settled_trades(conn, mode=mode))
    gate = evaluate_gate(m)
    d = asdict(m)
    return {
        "mode": mode,
        "n_settled": d["n"],
        "hit_rate": d["hit_rate"],
        "roi": d["roi"],
        "total_cost": d["total_cost"],
        "total_pnl": d.get("total_pnl"),
        "pnl_tstat": d.get("pnl_tstat"),
        "brier_model": d["brier_model"],
        "brier_market": d["brier_market"],
        "model_beats_market": d["model_beats_market"],
        "calibration": d["calibration"],
        "calibration_error": d["calibration_error"],
        "gate_passed": gate.passed,
        "gate_checks": [[label, ok, detail] for label, ok, detail in gate.checks],
    }


def mirror(conn: sqlite3.Connection, settings: Settings, *, full: bool = False) -> dict[str, int]:
    """Push new local rows to Supabase and append one graded metrics snapshot.

    Returns per-table counts. Never raises: a failure reports ``{"mirror_error": 1}``
    and the caller carries on.
    """
    counts: dict[str, int] = {}
    if not settings.has_supabase:
        return counts
    base = (settings.supabase_url or "").rstrip("/")
    try:
        with httpx.Client(headers=_headers(settings), timeout=_TIMEOUT) as client:
            for remote, (local, conflict, ts_col) in _TABLES.items():
                since = (
                    None if (full or ts_col is None) else _watermark(client, base, remote, ts_col)
                )
                rows = _rows(conn, local, since, ts_col)
                if rows:
                    counts[remote] = _upsert(client, base, remote, rows, conflict)
            r = client.post(
                f"{base}/rest/v1/kalshi_metrics",
                headers={"Prefer": "return=minimal"},
                json=[_metrics_row(conn)],
            )
            r.raise_for_status()
            counts["kalshi_metrics"] = 1
    except Exception as exc:  # noqa: BLE001 - deliberate: the trading pass outranks the board
        print(
            f"WARNING: Supabase mirror failed ({type(exc).__name__}: {exc}). "
            "Local SQLite is unaffected."
        )
        return {"mirror_error": 1}
    return counts
