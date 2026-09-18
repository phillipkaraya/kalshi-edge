"""The Supabase mirror must never be able to break a trading pass.

Context: the hosted board was rendering empty because `data/` is gitignored and both
Streamlit Cloud and Vercel run ephemeral filesystems, so the SQLite ledger never
reached them. The mirror fixes that, but it introduces a network call into a
previously offline-safe hourly job. These tests pin the properties that keep that
safe, and pin the serialization the board depends on.
"""

from __future__ import annotations

import httpx
import pytest

from kalshi_edge.config import Settings
from kalshi_edge.db.supabase_mirror import _TABLES, _metrics_row, _rows, mirror
from kalshi_edge.storage import connect


def _settings(**kw) -> Settings:
    return Settings(**kw)


# --- Fail-open ---------------------------------------------------------------


def test_mirror_is_a_noop_when_unconfigured(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    s = _settings(supabase_url=None, supabase_service_role_key=None)
    assert s.has_supabase is False
    assert mirror(conn, s) == {}
    conn.close()


def test_mirror_swallows_network_failure(tmp_path, monkeypatch, capsys) -> None:
    """A dead endpoint must return an error count, never raise into the pass."""
    conn = connect(tmp_path / "t.db")
    s = _settings(supabase_url="https://example.invalid", supabase_service_role_key="k")

    def boom(*a, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx.Client, "get", boom)
    monkeypatch.setattr(httpx.Client, "post", boom)

    assert mirror(conn, s) == {"mirror_error": 1}
    assert "Local SQLite is unaffected" in capsys.readouterr().out
    conn.close()


def test_mirror_swallows_http_error(tmp_path, monkeypatch) -> None:
    """A 4xx/5xx from PostgREST is also non-fatal."""
    conn = connect(tmp_path / "t.db")
    s = _settings(supabase_url="https://example.invalid", supabase_service_role_key="k")

    def bad(*a, **kw):
        req = httpx.Request("GET", "https://example.invalid")
        raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))

    monkeypatch.setattr(httpx.Client, "get", bad)
    monkeypatch.setattr(httpx.Client, "post", bad)

    assert mirror(conn, s) == {"mirror_error": 1}
    conn.close()


# --- Row shaping -------------------------------------------------------------


def test_local_autoincrement_id_is_never_sent(tmp_path) -> None:
    """Postgres owns its own identity column; a supplied id collides with the sequence."""
    conn = connect(tmp_path / "t.db")
    conn.execute(
        "insert into settlements (ticker, event_ticker, result, settled_ts, last_price)"
        " values ('T-A', 'T', 'yes', '2026-01-01T00:00:00+00:00', 0.9)"
    )
    conn.commit()
    rows = _rows(conn, "settlements", None, None)
    assert rows and "id" not in rows[0]
    assert rows[0]["ticker"] == "T-A"
    conn.close()


@pytest.mark.parametrize("remote,spec", sorted(_TABLES.items()))
def test_every_conflict_column_exists_locally(tmp_path, remote, spec) -> None:
    """A wrong on_conflict column silently duplicates rows instead of merging."""
    local, conflict, ts_col = spec
    conn = connect(tmp_path / "t.db")
    cols = {r[1] for r in conn.execute(f"pragma table_info({local})")}
    assert cols, f"{local} missing from the SQLite schema"
    for c in conflict.split(","):
        assert c in cols, f"{remote}: on_conflict column {c!r} not in {local}"
    if ts_col:
        assert ts_col in cols
    conn.close()


# --- Metrics serialization ---------------------------------------------------


def test_metrics_row_carries_every_gate_check(tmp_path) -> None:
    """The board renders these numbers and never derives them, so the shape is a contract."""
    conn = connect(tmp_path / "t.db")
    row = _metrics_row(conn)

    for key in (
        "n_settled",
        "hit_rate",
        "roi",
        "total_pnl",
        "total_cost",
        "brier_model",
        "brier_market",
        "model_beats_market",
        "calibration",
        "calibration_error",
        "gate_passed",
        "gate_checks",
    ):
        assert key in row, f"missing {key}"

    # Empty ledger: nothing settled, gate shut. This is the correct state until the
    # first in-window game resolves.
    assert row["n_settled"] == 0
    assert row["gate_passed"] is False

    # All five gate checks, each a [label, ok, detail] triple the UI can render.
    assert len(row["gate_checks"]) == 5
    for label, ok, detail in row["gate_checks"]:
        assert isinstance(label, str) and label
        assert isinstance(ok, bool)
        assert isinstance(detail, str)
    conn.close()


def test_metrics_row_is_json_serializable(tmp_path) -> None:
    """calibration and gate_checks go into jsonb columns; tuples must not leak."""
    import json

    conn = connect(tmp_path / "t.db")
    json.dumps(_metrics_row(conn))  # raises on any non-JSON type
    conn.close()
