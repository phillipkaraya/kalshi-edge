"""HARDENING.md items: fill reconciliation, pending exposure, fee reserve, netting,
order schema, and the real-time kill switch.

Each test names the failure it exists to prevent, because every one of these is a
quiet money bug rather than a crash.
"""

from __future__ import annotations

import sqlite3

import pytest

from kalshi_edge.execution.ledger import (
    OrderRecord,
    event_room_contracts,
    event_worst_case_exposure,
    get_positions,
    market_subject,
    pending_orders,
    position_contracts,
    record_order,
    total_exposure,
    update_order_fill,
)
from kalshi_edge.execution.reconcile import reconcile_pending
from kalshi_edge.kalshi.client import build_order_body, parse_fill
from kalshi_edge.model.fees import max_contracts_within_budget, order_fee
from kalshi_edge.storage import connect

EVENT = "KXNBAGAME-26JUN10SASNYK"
SAS = f"{EVENT}-SAS"
NYK = f"{EVENT}-NYK"


def _order(conn, ticker, side, count, price, status, filled=None, oid=None):
    return record_order(
        conn,
        OrderRecord(
            mode="demo",
            ticker=ticker,
            event_ticker=EVENT,
            side=side,
            action="buy",
            count=count,
            price=price,
            fee=order_fee(count, price),
            status=status,
            filled_count=count if filled is None and status == "filled" else (filled or 0),
            kalshi_order_id=oid,
        ),
    )


def _snapshot(conn):
    for t in (SAS, NYK):
        conn.execute(
            "INSERT INTO market_snapshots (ts, ticker, event_ticker) VALUES (?, ?, ?)",
            ("t", t, EVENT),
        )
    conn.commit()


# --- #1 fill reconciliation ---------------------------------------------------


class _Client:
    """Stands in for Kalshi. `authenticated` mirrors the real client's property."""

    authenticated = True

    def __init__(self, responses):
        self.responses = responses
        self.asked: list[str] = []

    def get_order(self, order_id):
        self.asked.append(order_id)
        r = self.responses[order_id]
        if isinstance(r, Exception):
            raise r
        return r


def test_pending_order_is_not_counted_as_a_trade_until_confirmed(tmp_path):
    """The bug: an accepted order was recorded as a full fill, so the consistency
    gate graded trades that never executed."""
    conn = connect(tmp_path / "t.db")
    _order(conn, SAS, "yes", 10, 0.50, "pending", oid="A")
    assert get_positions(conn, mode="demo") == []  # not a position yet
    assert len(pending_orders(conn, mode="demo")) == 1


def test_reconcile_records_the_real_partial_fill(tmp_path):
    conn = connect(tmp_path / "t.db")
    _order(conn, SAS, "yes", 10, 0.50, "pending", oid="A")
    client = _Client({"A": {"order": {"status": "executed", "fill_count": "4.00"}}})
    report = reconcile_pending(conn, client, mode="demo")
    assert (report.checked, report.partial) == (1, 1)
    row = conn.execute("SELECT status, count, filled_count FROM orders").fetchone()
    assert (row["status"], row["count"], row["filled_count"]) == ("filled", 10, 4)
    assert get_positions(conn, mode="demo")[0].contracts == 4  # 4, not the requested 10


def test_reconcile_leaves_the_row_pending_when_kalshi_cannot_be_reached(tmp_path):
    """A lookup failure must never be read as a fill."""
    conn = connect(tmp_path / "t.db")
    _order(conn, SAS, "yes", 10, 0.50, "pending", oid="A")
    report = reconcile_pending(conn, _Client({"A": RuntimeError("boom")}), mode="demo")
    assert (report.errors, report.filled) == (1, 0)
    assert conn.execute("SELECT status FROM orders").fetchone()["status"] == "pending"


def test_reconcile_never_records_more_than_was_requested(tmp_path):
    conn = connect(tmp_path / "t.db")
    _order(conn, SAS, "yes", 10, 0.50, "pending", oid="A")
    reconcile_pending(
        conn, _Client({"A": {"order": {"status": "executed", "fill_count": "999"}}}), mode="demo"
    )
    assert conn.execute("SELECT filled_count FROM orders").fetchone()["filled_count"] == 10


def test_canceled_with_no_fill_frees_the_capital(tmp_path):
    conn = connect(tmp_path / "t.db")
    _order(conn, SAS, "yes", 10, 0.50, "pending", oid="A")
    reconcile_pending(
        conn, _Client({"A": {"order": {"status": "canceled", "fill_count": "0"}}}), mode="demo"
    )
    row = conn.execute("SELECT status, filled_count FROM orders").fetchone()
    assert (row["status"], row["filled_count"]) == ("canceled", 0)
    assert total_exposure(conn, mode="demo") == 0.0  # no longer ties up cap room


def test_paper_mode_never_reconciles(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert reconcile_pending(conn, _Client({}), mode="paper").checked == 0


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"order": {"status": "executed", "fill_count": "5.00"}}, (5, "filled")),
        ({"order": {"status": "resting", "fill_count": "0"}}, (0, "pending")),
        ({"order": {"status": "canceled", "fill_count": "2"}}, (2, "canceled")),
        ({"order": {"fill_count": "3", "remaining_count": "0"}}, (3, "filled")),
        ({"order": {"fill_count": "3", "remaining_count": "7"}}, (3, "pending")),
        ({"order": {"taker_fill_count": "8", "remaining_count": "0"}}, (8, "filled")),
        ({}, (0, "pending")),
        ({"order": {"status": "weird"}}, (0, "pending")),
        ({"order": {"fill_count": "not-a-number"}}, (0, "pending")),
    ],
)
def test_parse_fill_defaults_to_unfilled_on_anything_unclear(payload, expected):
    assert parse_fill(payload) == expected


# --- #2 pending counts toward exposure ----------------------------------------


def test_resting_order_consumes_cap_room(tmp_path):
    """The bug: exposure filtered on status='filled', so a resting limit order was
    invisible to the caps and a second order could oversize the position."""
    conn = connect(tmp_path / "t.db")
    _order(conn, SAS, "yes", 10, 0.50, "pending", oid="A")
    assert position_contracts(conn, mode="demo", ticker=SAS, side="yes") == 10
    assert total_exposure(conn, mode="demo") > 0
    # ...but it is still not a *position* for display or grading.
    assert get_positions(conn, mode="demo") == []


def test_rejected_orders_never_consume_room(tmp_path):
    conn = connect(tmp_path / "t.db")
    _order(conn, SAS, "yes", 10, 0.50, "rejected")
    assert position_contracts(conn, mode="demo", ticker=SAS, side="yes") == 0
    assert total_exposure(conn, mode="demo") == 0.0


# --- #5 fee reserve -----------------------------------------------------------


@pytest.mark.parametrize("price", [0.01, 0.03, 0.25, 0.5, 0.77, 0.99])
@pytest.mark.parametrize("budget", [0.5, 1.0, 7.5, 50.0, 613.37])
def test_sizing_never_exceeds_its_budget_once_the_fee_is_paid(budget, price):
    n = max_contracts_within_budget(budget, price)
    assert n * price + order_fee(n, price) <= budget + 1e-9
    # and it is the LARGEST such n
    assert (n + 1) * price + order_fee(n + 1, price) > budget


def test_sizing_rejects_a_budget_too_small_for_one_contract():
    assert max_contracts_within_budget(0.50, 0.50) == 0  # 0.50 + 0.02 fee > 0.50


# --- #6 correlation-aware netting ---------------------------------------------


def test_market_subject_reads_the_team_off_the_ticker():
    assert market_subject(SAS) == "SAS"
    assert market_subject("NO-DASHES") == "DASHES"
    assert market_subject("plain") == "plain"


def test_opposite_sides_of_one_game_are_one_bet(tmp_path):
    """SAS-yes and NYK-no both pay iff SAS wins: worst case is the FULL combined cost,
    not something netted. Gross spend happened to agree here -- the point is that the
    netted view does not mistake this for a hedge."""
    conn = connect(tmp_path / "t.db")
    _snapshot(conn)
    _order(conn, SAS, "yes", 10, 0.50, "filled")
    _order(conn, NYK, "no", 10, 0.50, "filled")
    cost = 2 * (10 * 0.50 + order_fee(10, 0.50))
    assert event_worst_case_exposure(conn, mode="demo", event_ticker=EVENT) == pytest.approx(cost)


def test_a_real_hedge_is_credited(tmp_path):
    """SAS-yes and NYK-yes cannot both lose, so worst case is far below gross spend."""
    conn = connect(tmp_path / "t.db")
    _snapshot(conn)
    _order(conn, SAS, "yes", 10, 0.50, "filled")
    _order(conn, NYK, "yes", 10, 0.50, "filled")
    gross = 2 * (10 * 0.50 + order_fee(10, 0.50))
    worst = event_worst_case_exposure(conn, mode="demo", event_ticker=EVENT)
    assert worst < gross
    assert worst == pytest.approx(gross - 10)  # one leg always returns $1/contract


def test_one_sided_book_is_never_cheaper_than_gross_spend(tmp_path):
    """Netting must not become a loophole: with no offsetting leg, worst case == cost."""
    conn = connect(tmp_path / "t.db")
    _snapshot(conn)
    _order(conn, SAS, "yes", 10, 0.50, "filled")
    cost = 10 * 0.50 + order_fee(10, 0.50)
    assert event_worst_case_exposure(conn, mode="demo", event_ticker=EVENT) == pytest.approx(cost)


def test_unknown_outcome_universe_stays_conservative(tmp_path):
    """With no market data proving the game's outcomes, the 'something else wins'
    scenario must remain in play -- no snapshots, no hedge credit."""
    conn = connect(tmp_path / "t.db")  # deliberately no _snapshot()
    _order(conn, SAS, "yes", 10, 0.50, "filled")
    _order(conn, NYK, "yes", 10, 0.50, "filled")
    gross = 2 * (10 * 0.50 + order_fee(10, 0.50))
    assert event_worst_case_exposure(conn, mode="demo", event_ticker=EVENT) == pytest.approx(gross)


def test_event_room_gives_a_hedge_more_space_than_a_double_down(tmp_path):
    conn = connect(tmp_path / "t.db")
    _snapshot(conn)
    _order(conn, SAS, "yes", 40, 0.50, "filled")
    common = dict(
        conn=conn,
        mode="demo",
        event_ticker=EVENT,
        price=0.50,
        cap_dollars=30.0,
        fee_multiplier=0.07,
        limit=100,
    )
    hedge = event_room_contracts(**common, ticker=NYK, side="yes")
    doubling = event_room_contracts(**common, ticker=SAS, side="yes")
    assert hedge > doubling


# --- #7 order body schema -----------------------------------------------------


def test_v2_body_matches_the_documented_schema():
    body = build_order_body(ticker=SAS, side="yes", count=10, price_cents=42)
    assert body["ticker"] == SAS
    assert body["side"] == "bid"  # buying YES = bid on the YES book
    assert body["count"] == "10.00"  # fixed-point string, not int
    assert body["price"] == "0.4200"  # dollars, not cents
    assert body["time_in_force"] == "immediate_or_cancel"
    assert body["self_trade_prevention_type"] == "taker_at_cross"
    assert "action" not in body and "type" not in body and "yes_price" not in body


def test_buying_no_is_expressed_as_selling_yes_at_the_inverse_price():
    """The inversion that would otherwise put a real order on the wrong side."""
    body = build_order_body(ticker=SAS, side="no", count=5, price_cents=42)
    assert body["side"] == "ask"
    assert body["price"] == "0.5800"  # 1 - 0.42


def test_legacy_body_is_still_available_verbatim():
    body = build_order_body(ticker=SAS, side="yes", count=10, price_cents=42, schema="legacy")
    assert body == {
        "ticker": SAS,
        "action": "buy",
        "side": "yes",
        "count": 10,
        "type": "limit",
        "yes_price": 42,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(side="maybe", count=1, price_cents=42),
        dict(side="yes", count=0, price_cents=42),
        dict(side="yes", count=-1, price_cents=42),
        dict(side="yes", count=1, price_cents=None),
        dict(side="yes", count=1, price_cents=42, schema="v3"),
    ],
)
def test_malformed_orders_raise_rather_than_reaching_the_exchange(kwargs):
    with pytest.raises(ValueError):
        build_order_body(ticker=SAS, **kwargs)


# --- migration ----------------------------------------------------------------


def test_existing_database_gains_filled_count_without_losing_history(tmp_path):
    """An older store must not have its historical fills read as zero-filled."""
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """CREATE TABLE orders (
             id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, mode TEXT NOT NULL,
             ticker TEXT NOT NULL, event_ticker TEXT, side TEXT NOT NULL,
             action TEXT NOT NULL, count INTEGER NOT NULL, price REAL NOT NULL,
             fee REAL NOT NULL, status TEXT NOT NULL, reason TEXT, p_fair REAL,
             p_market REAL, ev_net REAL, kalshi_order_id TEXT);"""
    )
    old.execute(
        "INSERT INTO orders (ts,mode,ticker,side,action,count,price,fee,status) "
        "VALUES ('t','paper',?,'yes','buy',7,0.5,0.01,'filled')",
        (SAS,),
    )
    old.commit()
    old.close()

    conn = connect(path)  # runs the migration
    row = conn.execute("SELECT filled_count FROM orders").fetchone()
    assert row["filled_count"] == 7  # backfilled from count, not left at 0
    assert get_positions(conn, mode="paper")[0].contracts == 7
    connect(path)  # idempotent: opening again must not fail or double-apply


def test_update_order_fill_sets_count_and_status(tmp_path):
    conn = connect(tmp_path / "t.db")
    oid = _order(conn, SAS, "yes", 10, 0.50, "pending", oid="A")
    update_order_fill(conn, oid, filled_count=6, status="filled", reason="partial")
    row = conn.execute("SELECT status, filled_count, reason FROM orders").fetchone()
    assert (row["status"], row["filled_count"], row["reason"]) == ("filled", 6, "partial")
