"""One scheduled paper-trading pass.

Snapshots every live NBA market, ingests any new settlements, computes the edge,
logs the signal, and submits paper orders for actionable edges. Run on a cron so
the consistency gate accumulates settled trades over time without supervision.

Usage: ``uv run python -m kalshi_edge.paper_pass``
"""

from __future__ import annotations

from datetime import UTC, datetime

from kalshi_edge import storage
from kalshi_edge.backtest.settlement import ingest_settlements
from kalshi_edge.config import Settings
from kalshi_edge.data.fixtures import fixture_game_odds
from kalshi_edge.data.matcher import fair_value_for_market
from kalshi_edge.data.odds import get_game_odds_cached
from kalshi_edge.execution.engine import ExecutionEngine, ticket_from_edge
from kalshi_edge.execution.reconcile import reconcile_pending
from kalshi_edge.execution.risk import RiskConfig, RiskManager
from kalshi_edge.kalshi.client import KalshiClient
from kalshi_edge.kalshi.markets import fetch_markets, tradeable_markets
from kalshi_edge.model.edge import evaluate_edge


def run_once(settings: Settings | None = None) -> dict[str, int]:
    s = settings or Settings()
    conn = storage.connect(s.db_path)
    client = KalshiClient(s)
    engine = ExecutionEngine(s, conn, RiskManager(RiskConfig.from_settings(s), s.bankroll), client)
    counts = {
        "markets": 0,
        "signals": 0,
        "filled": 0,
        "pending": 0,
        "rejected": 0,
        "settled": 0,
        "reconciled": 0,
    }
    try:
        counts["settled"] = ingest_settlements(conn, client, s.kalshi_series)
        # Confirm what actually filled BEFORE sizing anything new, so this pass
        # sees true exposure rather than last pass's optimistic guess. No-op in
        # paper mode, which fills by construction.
        counts["reconciled"] = reconcile_pending(conn, client, mode=s.execution_mode).changed
        games = get_game_odds_cached(s)[0] if s.has_odds_source else fixture_game_odds()
        now = datetime.now(UTC)
        for m in tradeable_markets(fetch_markets(client, s.kalshi_series)):
            counts["markets"] += 1
            storage.log_snapshot(conn, m)
            fv = fair_value_for_market(m, games, now=now)
            if fv is None:
                continue
            edge = evaluate_edge(
                fv.p_fair,
                m.yes_ask,
                m.no_ask,
                bankroll=s.bankroll,
                kelly_frac=s.kelly_fraction,
                max_fraction=s.max_position_fraction,
                min_ev=s.min_ev,
                fee_mult=s.fee_multiplier,
            )
            storage.log_signal(conn, m.ticker, m.implied_prob, fv, edge)
            counts["signals"] += 1
            ticket = ticket_from_edge(m, edge)
            if ticket is not None:
                result = engine.submit(ticket)
                counts["filled"] += result.status == "filled"
                counts["pending"] += result.status == "pending"
                counts["rejected"] += result.status == "rejected"
    finally:
        client.close()
        conn.close()
    return counts


def main() -> None:
    counts = run_once()
    print(datetime.now(UTC).isoformat(), "paper_pass", counts)


if __name__ == "__main__":
    main()
