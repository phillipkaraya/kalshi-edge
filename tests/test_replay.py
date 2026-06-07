"""Backtest / replay harness: ticker parsing, price sources, and end-to-end grading."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from kalshi_edge.backtest.replay import (
    PricedEntry,
    SettledMarketRow,
    _book_probs_for,
    _model_fair_for,
    _odds_price,
    _parse_settled_ticker,
    _snapshot_price,
    _synthetic_price,
    format_report,
    load_settled_markets,
    main,
    price_settled_market,
    replay,
)
from kalshi_edge.backtest.settlement import record_settlement
from kalshi_edge.config import Settings
from kalshi_edge.data.odds import GameOdds
from kalshi_edge.storage import connect


def _settle(conn, ticker: str, result: str, last_price: float) -> None:
    record_settlement(
        conn,
        ticker=ticker,
        event_ticker=ticker.rsplit("-", 1)[0],
        result=result,
        last_price=last_price,
    )


def _snap(
    conn,
    ticker: str,
    *,
    yes_bid: float,
    yes_ask: float,
    no_bid: float,
    no_ask: float,
) -> None:
    cols = (
        "ts, ticker, event_ticker, yes_bid, yes_ask, no_bid, no_ask, "
        "last_price, volume, open_interest, spread"
    )
    conn.execute(
        f"INSERT INTO market_snapshots ({cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now(UTC).isoformat(),
            ticker,
            ticker.rsplit("-", 1)[0],
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            round((yes_bid + yes_ask) / 2, 4),
            100.0,
            50.0,
            round(yes_ask - yes_bid, 4),
        ),
    )
    conn.commit()


# --- ticker parsing ---------------------------------------------------------


def test_parse_settled_ticker_basic() -> None:
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    assert pg.away == "SAS" and pg.home == "OKC"
    assert pg.yes_tricode == "SAS"
    assert pg.game_date is not None and pg.game_date.month == 5 and pg.game_date.day == 30


def test_parse_settled_ticker_no_side() -> None:
    pg = _parse_settled_ticker("KXNBAGAME-26MAY25NYKCLE-CLE")
    assert pg is not None
    assert {pg.away, pg.home} == {"NYK", "CLE"}
    assert pg.yes_tricode == "CLE"


def test_parse_settled_ticker_rejects_garbage() -> None:
    assert _parse_settled_ticker("NOT-A-TICKER") is None
    assert _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-ZZZ") is None  # ZZZ not a team


# --- DB load ----------------------------------------------------------------


def test_load_settled_markets_filters_and_limits(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    _settle(conn, "KXNBAGAME-26MAY30SASOKC-SAS", "yes", 0.99)
    _settle(conn, "KXNBAGAME-26MAY30SASOKC-OKC", "no", 0.01)
    _settle(conn, "KXWNBAGAME-26MAY30LASNYL-LAS", "yes", 0.99)
    rows = load_settled_markets(conn, series_prefix="KXNBAGAME")
    assert len(rows) == 2
    assert all(r.ticker.startswith("KXNBAGAME") for r in rows)
    limited = load_settled_markets(conn, series_prefix="KXNBAGAME", limit=1)
    assert len(limited) == 1
    conn.close()


# --- price sources ----------------------------------------------------------


def test_snapshot_price_is_true_point_in_time(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    _snap(
        conn,
        "KXNBAGAME-26MAY30SASOKC-SAS",
        yes_bid=0.40,
        yes_ask=0.42,
        no_bid=0.58,
        no_ask=0.60,
    )
    entry = _snapshot_price(conn, "KXNBAGAME-26MAY30SASOKC-SAS")
    assert entry is not None
    assert entry.source == "snapshot"
    assert entry.p_market == 0.41  # bid/ask mid
    assert entry.yes_ask == 0.42 and entry.no_ask == 0.60
    conn.close()


def test_snapshot_price_absent_returns_none(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    assert _snapshot_price(conn, "KXNBAGAME-26MAY30SASOKC-SAS") is None
    conn.close()


def _odds_games() -> list[GameOdds]:
    # SAS at home vs OKC, books ~62% SAS.
    return [GameOdds("SAS", "OKC", None, [0.61, 0.62, 0.63], [0.39, 0.38, 0.37], source="odds_api")]


def test_book_probs_for_yes_side() -> None:
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    probs = _book_probs_for(pg, _odds_games())
    assert probs == [0.61, 0.62, 0.63]


def test_odds_price_uses_consensus() -> None:
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    entry = _odds_price(pg, _odds_games())
    assert entry is not None
    assert entry.source == "odds"
    assert entry.p_market == 0.62  # median consensus
    assert entry.book_probs == [0.61, 0.62, 0.63]


def test_odds_price_none_when_game_uncovered() -> None:
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    assert _odds_price(pg, []) is None  # no odds at all


def test_synthetic_price_is_deterministic_and_independent() -> None:
    row = SettledMarketRow("KXNBAGAME-26MAY30SASOKC-SAS", "x", "yes", 0.99)
    e1 = _synthetic_price(row, random.Random(7))
    e2 = _synthetic_price(row, random.Random(7))
    assert e1 == e2  # deterministic for a fixed seed
    assert e1.source == "synthetic"
    assert e1.book_probs  # has an independent model signal
    # Market price and book consensus are not forced equal (the disagreement is the edge).
    assert 0.0 < e1.p_market < 1.0


# --- model fair value (real path) -------------------------------------------


def test_model_fair_from_books_can_disagree_with_market() -> None:
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    # Market sits at 0.50 but the books say 0.62 -> the model should see YES value.
    entry = PricedEntry(
        p_market=0.50, yes_ask=0.50, no_ask=0.50, book_probs=[0.61, 0.62, 0.63], source="odds"
    )
    fv = _model_fair_for(pg, entry)
    assert fv.p_fair == 0.62  # devigged consensus via the real fair_value
    assert fv.source == "consensus"


def test_model_fair_falls_back_to_market_without_books() -> None:
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    entry = PricedEntry(p_market=0.55, yes_ask=0.55, no_ask=0.45, book_probs=[], source="snapshot")
    fv = _model_fair_for(pg, entry)
    assert abs(fv.p_fair - 0.55) < 1e-6  # no independent signal -> fair == market
    assert fv.source == "market-implied"


def test_price_chain_prefers_snapshot_over_synthetic(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    _snap(
        conn,
        "KXNBAGAME-26MAY30SASOKC-SAS",
        yes_bid=0.40,
        yes_ask=0.42,
        no_bid=0.58,
        no_ask=0.60,
    )
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    row = SettledMarketRow("KXNBAGAME-26MAY30SASOKC-SAS", "x", "yes", 0.99)
    entry = price_settled_market(conn, row, pg, _odds_games(), random.Random(7))
    assert entry is not None
    assert entry.source == "snapshot"  # snapshot beats odds + synthetic
    assert entry.book_probs == [0.61, 0.62, 0.63]  # but enriched with real books
    conn.close()


# --- end to end -------------------------------------------------------------


def _seed_settlements(conn, n: int = 120) -> None:
    rng = random.Random(3)
    teams = ["SAS", "OKC", "NYK", "CLE", "BOS", "DEN", "MIA", "LAL"]
    for i in range(n):
        a, h = rng.sample(teams, 2)
        # Synthetic ticker; date varies so they're distinct.
        day = 10 + (i % 18)
        ticker = f"KXNBAGAME-26MAY{day:02d}{a}{h}-{a}"
        _settle(conn, ticker, "yes" if rng.random() < 0.5 else "no", 0.99)


def test_replay_end_to_end_grades_and_gates(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    _seed_settlements(conn, 120)
    s = Settings(db_path=tmp_path / "t.db")  # type: ignore[arg-type]
    result = replay(s, series_prefix="KXNBAGAME", use_live_odds=False, conn=conn)
    assert result.n_markets >= 100
    assert result.metrics.n > 0  # produced gradeable trades
    assert result.source_counts.get("synthetic", 0) == result.metrics.n  # offline path
    # Grader ran: metrics + gate are populated and internally consistent.
    assert 0.0 <= result.metrics.hit_rate <= 1.0
    assert result.metrics.brier_model is not None
    assert len(result.gate.checks) == 5
    # The report renders and flags the synthetic source.
    report = format_report(result)
    assert "SYNTHETIC" in report
    assert "OFFLINE GATE" in report
    conn.close()


def test_replay_no_synthetic_skips_when_no_real_prices(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    _seed_settlements(conn, 20)
    s = Settings(db_path=tmp_path / "t.db")  # type: ignore[arg-type]
    result = replay(
        s, series_prefix="KXNBAGAME", use_live_odds=False, allow_synthetic=False, conn=conn
    )
    # No snapshots, no odds, synthetic disabled -> nothing gradeable.
    assert result.metrics.n == 0
    assert result.n_skipped == result.n_markets
    conn.close()


def test_replay_real_snapshot_path_produces_real_trade(tmp_path) -> None:
    """A snapshotted market + book disagreement yields a genuine (non-synthetic) trade."""
    conn = connect(tmp_path / "t.db")
    _settle(conn, "KXNBAGAME-26MAY30SASOKC-SAS", "yes", 0.99)
    # Kalshi at ~0.50 while books (below) say ~0.62 -> YES edge.
    _snap(
        conn,
        "KXNBAGAME-26MAY30SASOKC-SAS",
        yes_bid=0.49,
        yes_ask=0.50,
        no_bid=0.50,
        no_ask=0.51,
    )
    # Rely on snapshot price + book enrichment via a direct price call to assert the edge.
    pg = _parse_settled_ticker("KXNBAGAME-26MAY30SASOKC-SAS")
    assert pg is not None
    row = SettledMarketRow("KXNBAGAME-26MAY30SASOKC-SAS", "x", "yes", 0.99)
    entry = price_settled_market(conn, row, pg, _odds_games(), random.Random(0))
    assert entry is not None and entry.source == "snapshot"
    fv = _model_fair_for(pg, entry)
    assert fv.p_fair > entry.p_market  # model sees YES underpriced
    conn.close()


# --- CLI --------------------------------------------------------------------


def test_cli_main_runs(tmp_path, capsys, monkeypatch) -> None:
    conn = connect(tmp_path / "t.db")
    _seed_settlements(conn, 110)
    conn.close()
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    main(["--sport", "nba", "--no-live-odds", "--bins", "8"])
    out = capsys.readouterr().out
    assert "BACKTEST / REPLAY" in out
    assert "OFFLINE GATE" in out
