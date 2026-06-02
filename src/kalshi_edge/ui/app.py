"""Kalshi Edge -- NBA Edge Board (Slice 1).

For every live Kalshi NBA market we compute an independent fair value (devigged
sportsbook consensus), the edge vs the market price, the fee-aware EV, and a
fractional-Kelly size -- then rank by EV so the best opportunities surface first.
Without an odds API key it runs on clearly-labelled DEMO odds so the full board
still renders. Run: ``uv run streamlit run src/kalshi_edge/ui/app.py``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

import pandas as pd
import streamlit as st

from kalshi_edge import storage
from kalshi_edge.config import Settings
from kalshi_edge.data.fixtures import fixture_game_odds
from kalshi_edge.data.matcher import fair_value_for_market
from kalshi_edge.data.odds import OddsApiClient
from kalshi_edge.kalshi.client import KalshiClient
from kalshi_edge.kalshi.markets import fetch_markets, tradeable_markets
from kalshi_edge.model.arbitrage import two_sided_lock
from kalshi_edge.model.edge import evaluate_edge
from kalshi_edge.model.momentum import momentum_signal

st.set_page_config(page_title="Kalshi Edge -- NBA", page_icon="🏀", layout="wide")

SPREAD_LIQUID = 0.03
_NEG_INF = float("-inf")


def _resolve_games(settings: Settings) -> tuple[list, bool]:
    """Return (game odds, fixture_mode). Falls back to demo fixture without a key."""
    if not settings.has_odds_source:
        return fixture_game_odds(), True
    try:
        return OddsApiClient(settings).get_game_odds(), False
    except Exception:  # noqa: BLE001 -- any odds failure -> safe demo fallback
        return fixture_game_odds(), True


@st.cache_data(ttl=30, show_spinner="Fetching Kalshi markets + odds...")
def analyze(
    env: str,
    series: tuple[str, ...],
    bankroll: float,
    kelly_frac: float,
    max_frac: float,
    min_ev: float,
    fee_mult: float,
) -> tuple[pd.DataFrame, list, bool]:
    settings = Settings(kalshi_env=cast(Literal["prod", "demo"], env))
    with KalshiClient(settings) as client:
        markets = tradeable_markets(fetch_markets(client, list(series)))
    games, fixture_mode = _resolve_games(settings)
    now = datetime.now(UTC)

    rows: list[dict] = []
    records: list[tuple] = []
    for m in markets:
        fv = fair_value_for_market(m, games, now=now)
        edge = None
        if fv is not None:
            edge = evaluate_edge(
                fv.p_fair,
                m.yes_ask,
                m.no_ask,
                bankroll=bankroll,
                kelly_frac=kelly_frac,
                max_fraction=max_frac,
                min_ev=min_ev,
                fee_mult=fee_mult,
            )
        records.append((m, fv, edge))
        actionable = edge is not None and edge.side != "none"
        mom = momentum_signal(m)
        arb = two_sided_lock(m.yes_ask, m.no_ask, fee_mult=fee_mult)
        rows.append(
            {
                "Game": m.title,
                "Team (Yes)": m.yes_sub_title,
                "Mkt %": None if m.implied_prob is None else round(m.implied_prob * 100, 1),
                "Fair %": None if fv is None else round(fv.p_fair * 100, 1),
                "Gap pp": None
                if (fv is None or m.implied_prob is None)
                else round((fv.p_fair - m.implied_prob) * 100, 1),
                "Bet": edge.side.upper() if actionable else "—",
                "EV ¢/ct": round((edge.ev_net or 0.0) * 100, 2) if actionable else None,
                "Size": edge.suggested_contracts if actionable else 0,
                "Conf": None if fv is None else fv.confidence,
                "Mom": {"up": "▲", "down": "▼", "flat": "·"}[mom.label],
                "Arb": "🔒" if arb.is_arb else "",
                "Spread ¢": None if m.spread is None else round(m.spread * 100, 1),
                "Vol": m.volume,
                "Closes": m.close_time,
                "_ev": edge.ev_net if actionable else _NEG_INF,
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["_ev", "Vol"], ascending=[False, False], na_position="last")
        df = df.drop(columns=["_ev"])
    return df, records, fixture_mode


# --- Sidebar ----------------------------------------------------------------
base = Settings()
st.sidebar.title("🏀 Kalshi Edge")
env = st.sidebar.selectbox("Environment", ["prod", "demo"], index=0)
series_input = st.sidebar.text_input("Series tickers (comma-sep)", ", ".join(base.kalshi_series))
series = tuple(s.strip() for s in series_input.split(",") if s.strip())

st.sidebar.subheader("Sizing")
bankroll = st.sidebar.number_input("Bankroll ($)", value=float(base.bankroll), step=100.0)
kelly_frac = st.sidebar.slider("Kelly fraction", 0.0, 1.0, base.kelly_fraction, 0.05)
max_frac = st.sidebar.slider(
    "Max position (% bankroll)", 0.0, 0.25, base.max_position_fraction, 0.01
)
min_ev = st.sidebar.slider("Min EV ¢/contract", 0.0, 0.10, base.min_ev, 0.005)

if st.sidebar.button("↻ Refresh"):
    analyze.clear()
st.sidebar.caption(
    "Reads are public. Add `ODDS_API_KEY` / `BALLDONTLIE_API_KEY` for live fair value; "
    "otherwise the board uses labelled DEMO odds."
)

# --- Main -------------------------------------------------------------------
st.title("NBA Edge Board")
st.caption(
    "Independent fair value (devigged consensus) vs Kalshi's price -> edge, fee-aware EV, "
    "and fractional-Kelly size. Ranked by EV."
)

try:
    df, records, fixture_mode = analyze(
        env, series, bankroll, kelly_frac, max_frac, min_ev, base.fee_multiplier
    )
except Exception as exc:  # noqa: BLE001 -- surface fetch/parse errors in the UI
    st.error(f"Failed to load board: {exc}")
    st.stop()

if fixture_mode:
    st.warning(
        "**DEMO odds** — no odds API key set, so fair values are illustrative (current "
        "Finals matchup only). Set `ODDS_API_KEY` or `BALLDONTLIE_API_KEY` for live edges."
    )

if df.empty:
    st.info("No tradeable markets for the selected series. Try `KXNBA` or `KXWNBAGAME`.")
    st.stop()

actionable = int((df["Bet"] != "—").sum())
c1, c2, c3, c4 = st.columns(4)
c1.metric("Markets", len(df))
c2.metric("Actionable edges", actionable)
c3.metric("Priced by", "DEMO" if fixture_mode else "live odds")
c4.metric("Σ suggested contracts", int(df["Size"].sum()))

if st.button("💾 Snapshot analysis to DB"):
    settings = Settings(kalshi_env=cast(Literal["prod", "demo"], env))
    conn = storage.connect(settings.db_path)
    try:
        for market, fv, edge in records:
            storage.log_snapshot(conn, market)
            if fv is not None and edge is not None:
                storage.log_signal(conn, market.ticker, market.implied_prob, fv, edge)
        st.success(
            f"Logged {len(records)} snapshots. Totals — "
            f"snapshots: {storage.count_rows(conn, 'market_snapshots')}, "
            f"signals: {storage.count_rows(conn, 'signals')}."
        )
    finally:
        conn.close()

st.dataframe(
    df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Mkt %": st.column_config.NumberColumn(format="%.1f%%"),
        "Fair %": st.column_config.NumberColumn(format="%.1f%%"),
        "Gap pp": st.column_config.NumberColumn(
            format="%+.1f", help="Fair minus market, YES perspective"
        ),
        "EV ¢/ct": st.column_config.NumberColumn(
            format="%.2f", help="Net EV per contract after fees"
        ),
        "Conf": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
        "Vol": st.column_config.NumberColumn(format="%.0f"),
    },
)
