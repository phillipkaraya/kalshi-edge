"""Kalshi Edge -- NBA Edge Board (Hybrid terminal).

For every live Kalshi NBA market we compute an independent fair value (devigged
sportsbook consensus), the edge vs the market price, the fee-aware EV, and a
fractional-Kelly size. The board surfaces the best opportunities as cards, maps
every market on a fair-vs-market scatter, and lists them in a color-graded table.
Without an odds API key it runs on clearly-labelled DEMO odds.
Run: ``uv run streamlit run src/kalshi_edge/ui/app.py``
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

import altair as alt
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
_GREEN = "#22c55e"
_RED = "#ef4444"
_GREY = "#6b7280"


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


# --- presentation helpers ---------------------------------------------------
def _cell_color(value: float | None, scale: float) -> str:
    """Green for positive, red for negative, alpha by magnitude. For Styler.map."""
    if value is None or pd.isna(value):
        return ""
    v = float(value)
    if v == 0.0:
        return ""
    alpha = min(0.6, 0.15 + abs(v) / scale)
    rgb = "34,197,94" if v > 0 else "239,68,68"
    return f"background-color: rgba({rgb},{alpha:.2f})"


def _style_table(frame: pd.DataFrame) -> pd.io.formats.style.Styler:
    return frame.style.map(lambda v: _cell_color(v, 8.0), subset=["EV ¢/ct"]).map(
        lambda v: _cell_color(v, 14.0), subset=["Gap pp"]
    )


def _render_top_edges(frame: pd.DataFrame) -> None:
    edges = frame[frame["Bet"] != "—"].head(4)
    if edges.empty:
        st.info("No actionable edges clear the EV threshold right now.")
        return
    for col, (_, r) in zip(st.columns(len(edges)), edges.iterrows(), strict=False):
        with col, st.container(border=True):
            side = r["Bet"]
            color = _GREEN if side == "YES" else _RED
            game = (r["Game"] or "")[:46]
            st.markdown(f"**{game}**")
            st.markdown(
                f"<span style='color:{color};font-weight:700'>BET {side}</span>"
                f" · {r['Team (Yes)']}",
                unsafe_allow_html=True,
            )
            st.metric("EV ¢/contract", f"{r['EV ¢/ct']:.2f}", delta=f"{r['Gap pp']:+.1f} pp gap")
            st.caption(
                f"size {int(r['Size'])} · conf {r['Conf']:.2f} · "
                f"mkt {r['Mkt %']:.0f}% → fair {r['Fair %']:.0f}%"
            )


def _edge_map(frame: pd.DataFrame) -> alt.LayerChart | None:
    d = frame.dropna(subset=["Mkt %", "Fair %"]).rename(
        columns={"Mkt %": "market", "Fair %": "fair", "EV ¢/ct": "ev"}
    )
    if d.empty:
        return None
    diagonal = (
        alt.Chart(pd.DataFrame({"market": [0, 100], "fair": [0, 100]}))
        .mark_line(strokeDash=[4, 4], color=_GREY)
        .encode(x="market", y="fair")
    )
    points = (
        alt.Chart(d)
        .mark_circle(opacity=0.85)
        .encode(
            x=alt.X("market", title="Market implied %"),
            y=alt.Y("fair", title="Fair value %"),
            color=alt.Color(
                "Bet",
                scale=alt.Scale(domain=["YES", "NO", "—"], range=[_GREEN, _RED, _GREY]),
                legend=alt.Legend(title="Bet"),
            ),
            size=alt.Size("Vol", legend=None),
            tooltip=["Game", "Bet", "market", "fair", "ev", "Size"],
        )
    )
    return (diagonal + points).properties(height=300)


# --- header -----------------------------------------------------------------
st.markdown(
    "<div style='display:flex;align-items:baseline;gap:12px;border-bottom:1px solid #30363d;"
    "padding-bottom:6px;margin-bottom:10px'>"
    "<span style='font-size:1.7rem;font-weight:800;letter-spacing:1px'>🏀 KALSHI EDGE</span>"
    f"<span style='color:{_GREEN};font-weight:700'>NBA</span>"
    "<span style='color:#8b949e;font-size:0.85rem;margin-left:auto'>"
    "fair value vs market · fee-aware EV · fractional-Kelly sizing</span></div>",
    unsafe_allow_html=True,
)

# --- sidebar ----------------------------------------------------------------
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

# --- main -------------------------------------------------------------------
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
c4.metric("Σ contracts", int(df["Size"].sum()))

st.subheader("Top edges")
_render_top_edges(df)

left, right = st.columns([3, 2])
with left:
    st.subheader("Edge board")
    st.dataframe(
        _style_table(df),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Mkt %": st.column_config.NumberColumn(format="%.1f%%"),
            "Fair %": st.column_config.NumberColumn(format="%.1f%%"),
            "Gap pp": st.column_config.NumberColumn(format="%+.1f", help="Fair minus market (YES)"),
            "EV ¢/ct": st.column_config.NumberColumn(format="%.2f", help="Net EV per contract"),
            "Conf": st.column_config.ProgressColumn(min_value=0.0, max_value=1.0, format="%.2f"),
            "Vol": st.column_config.NumberColumn(format="%.0f"),
        },
    )
with right:
    st.subheader("Edge map")
    chart = _edge_map(df)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True)
    st.caption("Dashed line = fair ≈ market. Above = YES underpriced; below = overpriced.")

with st.expander("⚙ Data / logging"):
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
