"""Go-Live Preflight (Slice 5).

Shows every interlock that must be green before real money can trade, plus the
manual steps only a human can perform. The app never self-arms live trading.
"""

from __future__ import annotations

import streamlit as st

from kalshi_edge import storage
from kalshi_edge.backtest.consistency import compute_metrics, synthetic_settled_trades
from kalshi_edge.backtest.settlement import build_settled_trades
from kalshi_edge.config import Settings
from kalshi_edge.execution.live import live_preflight

st.set_page_config(page_title="Go Live — Kalshi Edge", page_icon="🚦", layout="wide")
st.title("🚦 Go-Live Preflight")

base = Settings()
demo = st.toggle("Grade on synthetic demo trades", value=True)

if demo:
    metrics = compute_metrics(synthetic_settled_trades())
else:
    conn = storage.connect(base.db_path)
    try:
        metrics = compute_metrics(build_settled_trades(conn, mode="paper"))
    except Exception:  # noqa: BLE001
        metrics = compute_metrics([])
    finally:
        conn.close()

pf = live_preflight(base, metrics)

st.subheader("Automated interlocks")
for check in pf.checks:
    st.write(f"{'✅' if check.ok else '❌'} **{check.name}** — {check.detail}")

if pf.go:
    st.success("CLEARED FOR LIVE — all interlocks green.")
else:
    st.error("NOT CLEARED — live trading is refused. Real-money orders cannot be placed.")

st.divider()
st.subheader("Manual steps only you can do")
st.markdown(
    """
1. Confirm **NBA sports contracts are tradable in Georgia** on your Kalshi account.
2. Generate a Kalshi API key; set `KALSHI_KEY_ID` and `KALSHI_PRIVATE_KEY_PATH`.
3. Fund the account; start at the **minimum caps** configured in `.env`.
4. Let the **consistency gate pass on real settled paper trades** (not the synthetic demo).
5. Set `EXECUTION_MODE=live` and `LIVE_ENABLED=true`.
6. Keep the kill switch reachable: `KILL_SWITCH=true` halts all order placement instantly.
"""
)
st.caption("Even with every interlock green, only you flip LIVE_ENABLED — the app never self-arms.")
