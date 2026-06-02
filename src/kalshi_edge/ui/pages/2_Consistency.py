"""Consistency & Live Gate (Slice 3).

Grades settled paper trades (ROI / Brier / calibration) and shows whether the
live-trading gate is satisfied. Live stays locked until the gate passes AND a
human explicitly arms it.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from kalshi_edge import storage
from kalshi_edge.backtest.consistency import (
    GateThresholds,
    compute_metrics,
    evaluate_gate,
    synthetic_settled_trades,
)
from kalshi_edge.backtest.settlement import build_settled_trades, ingest_settlements
from kalshi_edge.config import Settings
from kalshi_edge.kalshi.client import KalshiClient

st.set_page_config(page_title="Consistency — Kalshi Edge", page_icon="✅", layout="wide")
st.title("✅ Consistency & Live Gate")

base = Settings()

col_a, col_b = st.columns([1, 1])
with col_a:
    if st.button("⬇️ Ingest settled results from Kalshi"):
        conn = storage.connect(base.db_path)
        client = KalshiClient(base)
        try:
            n = ingest_settlements(conn, client, base.kalshi_series)
            st.success(f"Recorded {n} settled markets.")
        finally:
            client.close()
            conn.close()
with col_b:
    demo = st.toggle(
        "Use synthetic demo trades",
        value=True,
        help="No settled paper trades yet? Toggle on to exercise the harness with simulated data.",
    )

if demo:
    trades = synthetic_settled_trades()
else:
    conn = storage.connect(base.db_path)
    try:
        trades = build_settled_trades(conn, mode="paper")
    except Exception as exc:  # noqa: BLE001 -- stale schema / empty store
        st.warning(f"Could not read settled paper trades ({exc}). Toggle the demo on.")
        trades = []
    finally:
        conn.close()

if not trades:
    st.info(
        "No settled paper trades yet — let paper bets settle and ingest results, or use the demo."
    )
    st.stop()

m = compute_metrics(trades)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Settled trades", m.n)
c2.metric("Hit rate", f"{m.hit_rate:.1%}")
c3.metric("ROI (net)", f"{m.roi:.1%}")
c4.metric("Net P&L ($)", f"{m.total_pnl:,.2f}")

c5, c6 = st.columns(2)
c5.metric("Brier — model", m.brier_model, help="Lower is better")
c6.metric(
    "Brier — market",
    m.brier_market,
    delta="model wins" if m.model_beats_market else "market wins",
    delta_color="normal" if m.model_beats_market else "inverse",
)

st.subheader("Calibration")
if m.calibration:
    cal = pd.DataFrame(m.calibration, columns=["predicted", "observed", "n"])
    cal["ideal"] = cal["predicted"]
    st.line_chart(cal.set_index("predicted")[["observed", "ideal"]])
    st.caption("Well-calibrated predictions track the diagonal (`observed ≈ predicted`).")

st.subheader("Live-trading gate")
gate = evaluate_gate(m, GateThresholds())
for name, ok, detail in gate.checks:
    st.write(f"{'✅' if ok else '❌'} **{name}** — {detail}")

if gate.passed:
    st.success(
        "GATE PASSED. You may set `LIVE_ENABLED=true` to arm live trading (Slice 5). "
        "Even after passing, live stays human-gated."
    )
else:
    st.error("GATE NOT PASSED — live trading stays locked.")
st.caption(
    f"Current `live_enabled` = **{base.live_enabled}**. The gate is necessary but not "
    "sufficient; you still explicitly arm live."
)
