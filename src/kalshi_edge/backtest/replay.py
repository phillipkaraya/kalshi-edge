"""Historical backtest / replay harness for the edge model.

Replays the *live* decision path over already-settled markets so the consistency
gate (which normally needs >=100 *time-accumulated* settled paper trades) can be
exercised against real history instead of waiting weeks. For each settled market
we reconstruct the model's fair value, decide a side, size it, compute the fee,
and grade the result with the SAME functions the live engine uses
(``model.probability.fair_value`` -> ``model.edge.evaluate_edge`` ->
``model.fees.order_fee`` -> ``backtest.consistency``). No parallel reimplementation.

----------------------------------------------------------------------------
THE OPEN DESIGN QUESTION: where does the historical entry ``price`` / ``p_market``
come from? (Resolved here, with the evidence behind the choice.)
----------------------------------------------------------------------------
A backtest needs the *point-in-time* market price the model would have faced at
decision time. Three candidate sources were evaluated, in priority order:

(a) **Kalshi candlesticks / market-history endpoint.** NOT available: the client
    (``kalshi/client.py``) exposes only ``get_markets`` (which returns *terminal*
    prices for settled markets), ``get_balance``, and ``create_order`` -- there is
    no candlestick/history method. Adding one would mean a new authenticated
    endpoint, untested against the live API, for markets that have already closed.

(b) **A last-traded / settlement-adjacent price stored in the DB.** Present but
    UNUSABLE as an entry price: ``settlements.last_price`` is the *post-resolution*
    price -- in this DB every settled row is 0.99 (winner) or 0.01 (loser). Feeding
    that in as ``p_market`` / entry ``price`` is look-ahead bias (the model would
    "know" the outcome). We therefore use ``last_price`` ONLY to confirm/derive the
    resolution label, never as an entry price.

(c) **The devigged sportsbook consensus as a ``p_market`` proxy** (and the live
    market mid as the entry price). This is the faithful path -- it is exactly what
    the live engine uses -- BUT the free Odds API tier returns only current/upcoming
    games. A single cheap probe of the historical endpoint on the configured key
    returned HTTP 401 ``HISTORICAL_UNAVAILABLE_ON_FREE_USAGE_PLAN`` and cost zero
    quota (``x-requests-last: 0``). So point-in-time odds for *past* games are not
    available without a paid tier.

**Decision / price-source chain (see ``PriceSource``):** the harness prefers, per
market, in order:
  1. ``snapshot`` -- a real ``market_snapshots`` row at/just before the game
     (the scheduled paper pass records these; for any market snapshotted while it
     was live, this is a TRUE point-in-time price).
  2. ``odds`` -- the devigged consensus for that game from cached/live odds (true
     point-in-time when the game is current/upcoming and odds are available).
  3. ``synthetic`` -- a deterministic, clearly-labeled offline price model used
     ONLY when neither real source exists (the case for the ~200 already-settled
     May markets on the free tier). It still runs the real model + grader so the
     full pipeline and the gate mechanics are exercised end to end.

**LIMITATION (important):** for the markets currently in the DB there is no stored
point-in-time price and no free historical odds, so the default run uses the
``synthetic`` source. Those headline numbers demonstrate the *pipeline and the
gate*, not a validated edge. A trustworthy "does the model beat the market" verdict
needs one of: (i) forward-accumulated ``market_snapshots`` (the cron is already
writing these for live games -> they grade for real as games settle), or (ii) a
paid Odds API tier for true point-in-time devig. The report from ``main`` states
which source produced the numbers and flags ``synthetic`` loudly.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from ..config import Settings, get_settings
from ..data.devig import devig_two_way
from ..data.odds import GameOdds, get_game_odds_cached
from ..data.teams import TEAMS, ParsedGame, parse_ticker_date
from ..model.edge import evaluate_edge
from ..model.fees import order_fee
from ..model.probability import FairValue, fair_value
from ..storage import connect
from .consistency import (
    ConsistencyMetrics,
    GateStatus,
    GateThresholds,
    SettledTrade,
    compute_metrics,
    evaluate_gate,
)

_SYNTH_SEED = 7  # deterministic synthetic prices -> reproducible backtest


@dataclass(frozen=True)
class SettledMarketRow:
    """One settled market pulled from the DB, with whatever fields are available."""

    ticker: str
    event_ticker: str | None
    result: str  # "yes" | "no"
    last_price: float | None  # TERMINAL price (post-resolution); not an entry price


@dataclass(frozen=True)
class PricedEntry:
    """A reconstructed point-in-time entry for one settled market.

    The model side and the market side are kept as INDEPENDENT observations -- that
    disagreement is the edge:
      * ``p_market`` is Kalshi's implied P(YES) at (simulated) decision time, and
        ``yes_ask``/``no_ask`` are the prices the model would pay there.
      * ``book_probs`` are the devigged per-book probabilities the model consensuses
        into ``p_fair`` (via the real ``fair_value``). When odds cover the game these
        are the actual sportsbook numbers; for the synthetic source they are an
        independent draw, NOT a copy of ``p_market``.
    ``source`` records which tier produced these (snapshot|odds|synthetic).
    """

    p_market: float
    yes_ask: float
    no_ask: float
    book_probs: list[float]
    source: str


def load_settled_markets(
    conn: sqlite3.Connection, *, series_prefix: str | None = None, limit: int | None = None
) -> list[SettledMarketRow]:
    """Load resolved markets (known outcomes) from the existing ``settlements`` table.

    ``series_prefix`` filters by the ticker's leading series (e.g. ``KXNBAGAME``);
    ``limit`` caps the number of rows. Only YES/NO-resolved markets are returned.
    """
    sql = (
        "SELECT ticker, event_ticker, result, last_price FROM settlements "
        "WHERE result IN ('yes','no')"
    )
    params: list[object] = []
    if series_prefix:
        sql += " AND ticker LIKE ?"
        params.append(f"{series_prefix}%")
    sql += " ORDER BY settled_ts, ticker"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    return [
        SettledMarketRow(
            ticker=r["ticker"],
            event_ticker=r["event_ticker"],
            result=r["result"],
            last_price=r["last_price"],
        )
        for r in rows
    ]


def _parse_settled_ticker(ticker: str) -> ParsedGame | None:
    """Reconstruct (away, home, yes-team, date) from a settled game ticker alone.

    Settled markets have no live title/subtitle, but the ticker encodes everything:
    ``KXNBAGAME-26MAY30SASOKC-SAS`` -> date 2026-05-30, matchup SAS@OKC (the order in
    the event segment is AWAYHOME), YES backs the trailing tricode (SAS).
    """
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    event_seg, yes_tri = parts[1], parts[-1]
    if yes_tri not in TEAMS:
        return None
    game_date = parse_ticker_date(ticker)
    # Strip the leading date token (e.g. "26MAY30") to leave the team segment.
    team_seg = event_seg
    for i, ch in enumerate(event_seg):
        if ch.isalpha() and i >= 5:  # YYMON DD -> first alpha after the day digits
            team_seg = event_seg[i:]
            break
    # team_seg is two concatenated tricodes, AWAYHOME. Tricodes are 2-4 chars, so
    # try every split point and keep the one where both halves are real teams.
    away = home = None
    for cut in range(2, len(team_seg) - 1):
        a, h = team_seg[:cut], team_seg[cut:]
        if a in TEAMS and h in TEAMS:
            away, home = a, h
            break
    if not (away and home) or yes_tri not in (away, home):
        return None
    return ParsedGame(away=away, home=home, yes_tricode=yes_tri, game_date=game_date)


def _snapshot_price(conn: sqlite3.Connection, ticker: str) -> PricedEntry | None:
    """A TRUE point-in-time price from the earliest snapshot of this exact market.

    The scheduled paper pass writes ``market_snapshots`` for live games; if this
    settled market was ever snapshotted while open, its earliest snapshot is a
    genuine pre-settlement entry price.
    """
    row = conn.execute(
        """SELECT yes_bid, yes_ask, no_bid, no_ask FROM market_snapshots
           WHERE ticker = ? AND yes_ask IS NOT NULL AND no_ask IS NOT NULL
           ORDER BY ts ASC LIMIT 1""",
        (ticker,),
    ).fetchone()
    if row is None:
        return None
    yes_bid, yes_ask, no_ask = row["yes_bid"], row["yes_ask"], row["no_ask"]
    if yes_ask is None or no_ask is None:
        return None
    # Market-implied P(YES) from the bid/ask mid (same notion as Market.implied_prob).
    if yes_bid is not None and yes_ask is not None:
        p_market = round((yes_bid + yes_ask) / 2, 4)
    else:
        p_market = round(yes_ask, 4)
    # book_probs filled in by the caller from real odds if the game is covered.
    return PricedEntry(
        p_market=p_market, yes_ask=yes_ask, no_ask=no_ask, book_probs=[], source="snapshot"
    )


def _book_probs_for(parsed: ParsedGame, games: list[GameOdds]) -> list[float]:
    """Devigged per-book P(YES) for the parsed matchup's YES side, if odds cover it.

    Returns the raw per-book list (NOT the consensus) so the model can run the real
    ``fair_value`` over it -- the model side stays independent of the Kalshi price.
    """
    pair = frozenset({parsed.away, parsed.home})
    for g in games:
        if frozenset({g.home, g.away}) != pair:
            continue
        if parsed.yes_tricode == g.home and g.home_book_probs:
            return list(g.home_book_probs)
        if parsed.yes_tricode == g.away and g.away_book_probs:
            return list(g.away_book_probs)
    return []


def _odds_price(parsed: ParsedGame, games: list[GameOdds]) -> PricedEntry | None:
    """Point-in-time price proxied from the devig consensus when odds are available.

    Free tier only covers current/upcoming games, so this returns None for past
    games -- but it makes the harness correct the moment odds for a game exist. Here
    the devig consensus IS the market proxy, so model and market coincide and no edge
    is expected; the value is that the prices are real point-in-time numbers.
    """
    probs = _book_probs_for(parsed, games)
    if not probs:
        return None
    p = min(0.99, max(0.01, fair_value(probs).p_fair))
    return PricedEntry(
        p_market=round(p, 4),
        yes_ask=round(p, 4),
        no_ask=round(1.0 - p, 4),
        book_probs=probs,
        source="odds",
    )


def _synthetic_price(row: SettledMarketRow, rng: random.Random) -> PricedEntry:
    """Deterministic OFFLINE price model -- used only when no real source exists.

    Mirrors ``consistency.synthetic_settled_trades``: draw a latent ``true_p``, then
    derive TWO independent observations -- the Kalshi market price (``p_market``) and
    a sportsbook consensus slightly closer to truth (the model's edge thesis:
    devigged books beat Kalshi's retail line). They genuinely disagree, so real edges
    appear, and the model is modestly skilled rather than clairvoyant.

    IMPORTANT honesty note: the real outcome is already fixed (settlement data), so to
    correlate the price with it at all we MUST tilt ``true_p`` toward the result. We
    keep that tilt SMALL and noisy (latent prob stays near 0.5, ~+/-0.12) so per-bin
    observed rates are realistic and neither Brier collapses to 0/1. This is a bounded,
    clearly-labeled leak, NOT a clean backtest -- see the module docstring.
    """
    # Latent prob near a coin flip, nudged toward the realized side; heavy noise keeps
    # the market frequently wrong (a real book is ~60-65% accurate on close games).
    tilt = 0.12 if row.result == "yes" else -0.12
    true_p = min(0.85, max(0.15, 0.5 + tilt + rng.uniform(-0.18, 0.18)))
    p_market = min(0.95, max(0.05, true_p + rng.gauss(0.0, 0.05)))  # Kalshi line, noisier
    p_book = min(0.95, max(0.05, true_p + rng.gauss(0.0, 0.03)))  # books, closer to truth
    # Two synthetic books bracketing the consensus so dispersion/median behave normally.
    book_probs = [
        min(0.99, max(0.01, p_book - 0.01)),
        min(0.99, max(0.01, p_book + 0.01)),
    ]
    yes_ask = round(p_market, 2)
    no_ask = round(1.0 - p_market, 2)
    return PricedEntry(
        p_market=round(p_market, 4),
        yes_ask=yes_ask,
        no_ask=no_ask,
        book_probs=book_probs,
        source="synthetic",
    )


def _model_fair_for(parsed: ParsedGame, entry: PricedEntry) -> FairValue:
    """Reconstruct the model's fair value via the SAME path the live engine uses.

    The model side is the devigged sportsbook consensus over ``entry.book_probs``,
    run through the identical ``fair_value`` the live matcher calls -- so the model
    code under test is never bypassed. If a source produced NO independent books
    (e.g. a snapshot with no odds coverage), the model has nothing to disagree with,
    so it falls back to the market-implied prob (-> no edge, correctly no trade).
    """
    spread = round(max(0.0, entry.yes_ask + entry.no_ask - 1.0), 4)
    if entry.book_probs:
        return fair_value(entry.book_probs, kalshi_spread=spread, source="consensus")
    # No independent book signal: fair == market (exercise devig as an identity so the
    # real code path still runs), which yields zero edge and no trade.
    p = min(0.99, max(0.01, entry.p_market))
    p_yes, _ = devig_two_way(p, 1.0 - p)
    return fair_value([p_yes], kalshi_spread=spread, source="market-implied")


def price_settled_market(
    conn: sqlite3.Connection,
    row: SettledMarketRow,
    parsed: ParsedGame,
    games: list[GameOdds],
    rng: random.Random,
    *,
    allow_synthetic: bool = True,
) -> PricedEntry | None:
    """Resolve the entry price via the documented source chain (snapshot|odds|synthetic).

    A snapshot gives the real Kalshi point-in-time price; if odds also cover that
    game, the devigged books become the independent model signal (a genuine
    snapshot-vs-books backtest). Otherwise we fall to the odds proxy, then synthetic.
    """
    snap = _snapshot_price(conn, row.ticker)
    if snap is not None:
        return replace(snap, book_probs=_book_probs_for(parsed, games))
    odds = _odds_price(parsed, games)
    if odds is not None:
        return odds
    if allow_synthetic:
        return _synthetic_price(row, rng)
    return None


@dataclass(frozen=True)
class ReplayResult:
    trades: list[SettledTrade]
    metrics: ConsistencyMetrics
    gate: GateStatus
    source_counts: dict[str, int]  # how many trades came from each price source
    n_markets: int  # settled markets considered
    n_skipped: int  # markets we couldn't parse/price/trade


def replay(
    settings: Settings | None = None,
    *,
    series_prefix: str | None = "KXNBAGAME",
    limit: int | None = None,
    bins: int = 10,
    allow_synthetic: bool = True,
    use_live_odds: bool = True,
    thresholds: GateThresholds | None = None,
    conn: sqlite3.Connection | None = None,
) -> ReplayResult:
    """Replay the model over settled markets and grade it.

    Reuses the live decision path end to end: reconstruct ``p_fair`` (devig
    consensus -> ``fair_value``), take ``p_market`` + ask prices from the price
    source chain, decide the side and size with ``evaluate_edge``, compute the fee
    with ``order_fee``, and record ``outcome_yes`` from the settlement. Then runs
    ``compute_metrics`` + ``evaluate_gate``.
    """
    s = settings or get_settings()
    own_conn = conn is None
    conn = conn or connect(s.db_path)
    rng = random.Random(_SYNTH_SEED)
    try:
        rows = load_settled_markets(conn, series_prefix=series_prefix, limit=limit)
        # Pull odds ONCE (cached) so a true point-in-time price is used wherever the
        # game is current/upcoming. Best-effort: never fail the backtest on odds.
        games: list[GameOdds] = []
        if use_live_odds and s.has_odds_source:
            try:
                games = get_game_odds_cached(s)[0]
            except Exception:
                games = []

        trades: list[SettledTrade] = []
        source_counts: dict[str, int] = {}
        skipped = 0
        for row in rows:
            parsed = _parse_settled_ticker(row.ticker)
            if parsed is None:
                skipped += 1
                continue
            entry = price_settled_market(
                conn, row, parsed, games, rng, allow_synthetic=allow_synthetic
            )
            if entry is None:
                skipped += 1
                continue
            fv = _model_fair_for(parsed, entry)
            edge = evaluate_edge(
                fv.p_fair,
                entry.yes_ask,
                entry.no_ask,
                bankroll=s.bankroll,
                kelly_frac=s.kelly_fraction,
                max_fraction=s.max_position_fraction,
                min_ev=s.min_ev,
                fee_mult=s.fee_multiplier,
            )
            if (
                edge.side not in ("yes", "no")
                or edge.price is None
                or edge.suggested_contracts <= 0
            ):
                skipped += 1  # model declined this market (no actionable edge)
                continue
            fee = order_fee(edge.suggested_contracts, edge.price, multiplier=s.fee_multiplier)
            trades.append(
                SettledTrade(
                    ticker=row.ticker,
                    side=edge.side,
                    price=edge.price,
                    count=edge.suggested_contracts,
                    fee=fee,
                    p_fair=fv.p_fair,
                    p_market=entry.p_market,
                    outcome_yes=1 if row.result == "yes" else 0,
                )
            )
            source_counts[entry.source] = source_counts.get(entry.source, 0) + 1

        metrics = compute_metrics(trades, bins=bins)
        gate = evaluate_gate(metrics, thresholds)
        return ReplayResult(
            trades=trades,
            metrics=metrics,
            gate=gate,
            source_counts=source_counts,
            n_markets=len(rows),
            n_skipped=skipped,
        )
    finally:
        if own_conn:
            conn.close()


def dominant_source(source_counts: dict[str, int]) -> str:
    """The price source that produced the most trades (for labeling the report/UI)."""
    return max(source_counts, key=lambda k: source_counts[k]) if source_counts else "none"


def format_report(result: ReplayResult) -> str:
    """A clear, human-readable backtest report (printed by the CLI)."""
    m = result.metrics
    lines: list[str] = []
    lines.append("=" * 68)
    lines.append("BACKTEST / REPLAY  -  offline model-vs-market over settled markets")
    lines.append("=" * 68)
    src = result.source_counts
    dominant = dominant_source(src)
    lines.append(
        f"Settled markets: {result.n_markets}   traded: {m.n}   "
        f"skipped (unparseable/no-edge): {result.n_skipped}"
    )
    lines.append(f"Price source(s): {src or '{}'}  (dominant: {dominant})")
    if dominant == "synthetic":
        lines.append("  !! SYNTHETIC PRICES: no stored point-in-time price and no free historical")
        lines.append("     odds for these settled markets. Numbers show the PIPELINE + GATE, not a")
        lines.append("     validated edge. Trustworthy verdict needs forward snapshots or a paid")
        lines.append("     Odds API tier. See replay.py module docstring.")
    lines.append("-" * 68)
    if m.n == 0:
        lines.append("No gradeable trades produced.")
        lines.append("=" * 68)
        return "\n".join(lines)
    lines.append(f"Hit rate        : {m.hit_rate:.1%}")
    lines.append(
        f"ROI (net fees)  : {m.roi:.2%}   (net P&L ${m.total_pnl:,.2f} on ${m.total_cost:,.2f})"
    )
    lines.append(f"Brier  model    : {m.brier_model}")
    lines.append(f"Brier  market   : {m.brier_market}")
    verdict = "MODEL BEATS MARKET" if m.model_beats_market else "market beats model"
    lines.append(f"  -> {verdict}  (lower Brier is better)")
    lines.append(f"Calibration err : {m.calibration_error}")
    lines.append(f"PnL t-stat      : {m.pnl_tstat}")
    lines.append("-" * 68)
    lines.append("Calibration (predicted -> observed, n):")
    for pred, obs, n in m.calibration:
        lines.append(f"    {pred:>5}  ->  {obs:>5}   (n={n})")
    lines.append("-" * 68)
    lines.append(f"OFFLINE GATE: {'PASSED' if result.gate.passed else 'NOT PASSED'}")
    for name, ok, detail in result.gate.checks:
        lines.append(f"    [{'PASS' if ok else 'FAIL'}] {name} -- {detail}")
    lines.append("=" * 68)
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m kalshi_edge.backtest.replay",
        description="Replay the edge model over settled markets and grade it offline.",
    )
    p.add_argument(
        "--sport",
        default="nba",
        help="Sport to replay (nba -> KXNBAGAME, wnba -> KXWNBAGAME). Default: nba.",
    )
    p.add_argument("--limit", type=int, default=None, help="Max settled markets to replay.")
    p.add_argument("--bins", type=int, default=10, help="Calibration bins (default 10).")
    p.add_argument(
        "--no-synthetic",
        action="store_true",
        help="Disable the synthetic fallback (grade only markets with real point-in-time prices).",
    )
    p.add_argument(
        "--no-live-odds",
        action="store_true",
        help="Do not fetch live odds (skip the 'odds' price source entirely).",
    )
    return p


_SPORT_SERIES = {"nba": "KXNBAGAME", "wnba": "KXWNBAGAME"}


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    series_prefix = _SPORT_SERIES.get(args.sport.lower(), args.sport.upper())
    result = replay(
        series_prefix=series_prefix,
        limit=args.limit,
        bins=args.bins,
        allow_synthetic=not args.no_synthetic,
        use_live_odds=not args.no_live_odds,
    )
    print(format_report(result))
    print(datetime.now(UTC).isoformat(), "replay", {"traded": result.metrics.n})


if __name__ == "__main__":
    main()
