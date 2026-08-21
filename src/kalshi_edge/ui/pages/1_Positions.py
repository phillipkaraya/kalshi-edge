"""Positions & paper trading (Slice 2).

Runs the execution engine over the current edges in the selected mode and shows
the resulting positions + order log. ``paper`` fills are simulated against live
asks; ``demo`` needs Kalshi creds; ``live`` is refused until the Slice 3
consistency gate flips ``live_enabled``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal, cast

import pandas as pd
import streamlit as st

from kalshi_edge import storage
from kalshi_edge.config import Settings
from kalshi_edge.data.fixtures import fixture_game_odds
from kalshi_edge.data.matcher import fair_value_for_market
from kalshi_edge.data.odds import OddsApiClient
from kalshi_edge.execution.engine import ExecutionEngine, ticket_from_edge
from kalshi_edge.execution.ledger import get_orders, get_positions, total_exposure
from kalshi_edge.execution.risk import RiskConfig, RiskManager
from kalshi_edge.kalshi.client import KalshiClient
from kalshi_edge.kalshi.markets import fetch_markets, tradeable_markets
from kalshi_edge.model.edge import evaluate_edge

st.set_page_config(page_title="Positions — Kalshi Edge", page_icon="📈", layout="wide")
st.title("📈 Positions & Paper Trading")

base = Settings()
modes = ["paper", "demo", "live"]
mode = st.selectbox("Execution mode", modes, index=modes.index(base.execution_mode))
st.caption(
    f"DB `{base.db_path}` · live_enabled = **{base.live_enabled}** "
    "(stays False until the Slice 3 consistency gate passes). "
    "Kill switch via `KILL_SWITCH=true` in `.env`."
)


def _run_paper_pass(mode: str) -> tuple[int, int, int]:
    settings = Settings(execution_mode=cast(Literal["paper", "demo", "live"], mode))
    conn = storage.connect(settings.db_path)
    client = KalshiClient(settings)
    engine = ExecutionEngine(
        settings, conn, RiskManager(RiskConfig.from_settings(settings), settings.bankroll), client
    )
    filled = pending = rejected = 0
    try:
        markets = tradeable_markets(fetch_markets(client, settings.kalshi_series))
        games = (
            OddsApiClient(settings).get_game_odds()
            if settings.has_odds_source
            else fixture_game_odds()
        )
        now = datetime.now(UTC)
        for m in markets:
            fv = fair_value_for_market(m, games, now=now)
            if fv is None:
                continue
            edge = evaluate_edge(
                fv.p_fair,
                m.yes_ask,
                m.no_ask,
                bankroll=settings.bankroll,
                kelly_frac=settings.kelly_fraction,
                max_fraction=settings.max_position_fraction,
                min_ev=settings.min_ev,
                fee_mult=settings.fee_multiplier,
            )
            ticket = ticket_from_edge(m, edge)
            if ticket is None:
                continue
            result = engine.submit(ticket)
            # demo/live orders come back "pending" -- accepted by Kalshi but not yet
            # confirmed filled. Counting those as rejections would tell the user their
            # orders failed when they are actually resting on the book.
            if result.status == "filled":
                filled += 1
            elif result.status == "pending":
                pending += 1
            else:
                rejected += 1
    finally:
        client.close()
        conn.close()
    return filled, pending, rejected


if st.button(f"▶️ Run a {mode} pass over current edges"):
    filled, pending, rejected = _run_paper_pass(mode)
    summary = f"{filled} filled, {rejected} rejected"
    if pending:
        summary += f", {pending} resting (awaiting fill confirmation)"
    st.success(f"{mode} pass complete — {summary}.")

conn = storage.connect(base.db_path)
try:
    positions = get_positions(conn, mode=mode)
    exposure = total_exposure(conn, mode=mode)
    orders = get_orders(conn, mode=mode)
finally:
    conn.close()

c1, c2, c3 = st.columns(3)
c1.metric("Open positions", len(positions))
c2.metric("Exposure ($)", f"{exposure:,.2f}")
c3.metric("Orders logged", len(orders))

st.subheader("Open positions")
if positions:
    st.dataframe(
        pd.DataFrame([asdict(p) for p in positions]), hide_index=True, use_container_width=True
    )
else:
    st.info("No open positions yet — run a pass above.")

st.subheader("Order log")
if orders:
    st.dataframe(pd.DataFrame([dict(o) for o in orders]), hide_index=True, use_container_width=True)
else:
    st.info("No orders logged yet.")
