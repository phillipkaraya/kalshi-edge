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
from kalshi_edge.backtest.replay import dominant_source, replay
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

# --- Backtest (offline replay) ------------------------------------------------
# DISTINCT from the live paper accumulation above: this replays the model over
# already-settled markets so the gate can be pressure-tested without waiting for
# 100+ paper trades to settle over time. Clearly labeled offline.
st.divider()
st.subheader("🧪 Backtest — offline replay (settled history)")
st.caption(
    "Replays the **same** model path (devig → fair value → edge → Kelly → fees) over "
    "settled markets and grades it. This is an OFFLINE backtest, separate from the live "
    "paper trades above — it short-circuits the time-gated 100-trade requirement."
)

with st.expander("Run backtest replay", expanded=False):
    bt_cols = st.columns([1, 1, 1])
    bt_limit = bt_cols[0].number_input("Max markets", min_value=0, value=0, step=50, help="0 = all")
    bt_bins = bt_cols[1].number_input("Calibration bins", min_value=2, max_value=20, value=10)
    bt_synth = bt_cols[2].toggle(
        "Allow synthetic prices",
        value=True,
        help=(
            "Settled markets here have no stored point-in-time price and no free historical "
            "odds, so a labeled synthetic price model is used. Turn OFF to grade only markets "
            "with REAL point-in-time prices (snapshots/odds)."
        ),
    )
    if st.button("▶️ Run replay", key="run_replay"):
        with st.spinner("Replaying model over settled markets…"):
            try:
                bt = replay(
                    base,
                    series_prefix="KXNBAGAME",
                    limit=int(bt_limit) or None,
                    bins=int(bt_bins),
                    allow_synthetic=bt_synth,
                )
                st.session_state["backtest_result"] = bt
            except Exception as exc:  # noqa: BLE001 -- surface any replay error in the UI
                st.error(f"Replay failed: {exc}")

bt = st.session_state.get("backtest_result")
if bt is not None:
    if bt.metrics.n == 0:
        st.warning(
            f"No gradeable trades from {bt.n_markets} settled markets "
            f"({bt.n_skipped} skipped — unparseable, unpriced, or no edge)."
        )
    else:
        bm = bt.metrics
        dominant = dominant_source(bt.source_counts)
        if dominant == "synthetic":
            st.warning(
                "**SYNTHETIC PRICES** — no stored point-in-time price and no free historical "
                "odds for these settled markets. These numbers demonstrate the **pipeline and "
                "the gate**, not a validated edge. A trustworthy verdict needs forward-"
                "accumulated snapshots or a paid Odds API tier.",
                icon="⚠️",
            )
        else:
            st.info(f"Price source(s): {bt.source_counts} (dominant: {dominant}).", icon="📈")

        bc1, bc2, bc3, bc4 = st.columns(4)
        bc1.metric("Backtest trades", bm.n)
        bc2.metric("Hit rate", f"{bm.hit_rate:.1%}")
        bc3.metric("ROI (net)", f"{bm.roi:.1%}")
        bc4.metric("Net P&L ($)", f"{bm.total_pnl:,.2f}")

        bc5, bc6, bc7 = st.columns(3)
        bc5.metric("Brier — model", bm.brier_model, help="Lower is better")
        bc6.metric(
            "Brier — market",
            bm.brier_market,
            delta="model wins" if bm.model_beats_market else "market wins",
            delta_color="normal" if bm.model_beats_market else "inverse",
        )
        bc7.metric("PnL t-stat", bm.pnl_tstat, help=f"calibration err {bm.calibration_error}")

        bt_gate = bt.gate
        st.write("**Offline gate** (same thresholds as the live gate):")
        for name, ok, detail in bt_gate.checks:
            st.write(f"{'✅' if ok else '❌'} **{name}** — {detail}")
        if bt_gate.passed:
            st.success(
                "OFFLINE GATE PASSED on this history. This is evidence, not authorization — "
                "the LIVE gate still grades real settled paper trades before live can arm."
            )
        else:
            st.error("OFFLINE GATE NOT PASSED on this history.")
