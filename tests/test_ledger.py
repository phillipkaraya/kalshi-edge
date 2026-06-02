"""Order ledger + position aggregation."""

from __future__ import annotations

import pytest

from kalshi_edge.execution.ledger import (
    OrderRecord,
    get_orders,
    get_positions,
    position_contracts,
    record_order,
    total_exposure,
)
from kalshi_edge.storage import connect


def _buy(ticker: str, count: int, price: float, fee: float) -> OrderRecord:
    return OrderRecord(
        mode="paper",
        ticker=ticker,
        event_ticker=ticker.rsplit("-", 1)[0],
        side="yes",
        action="buy",
        count=count,
        price=price,
        fee=fee,
        status="filled",
    )


def test_positions_aggregate(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    record_order(conn, _buy("KX-1-A", 100, 0.50, 0.02))
    record_order(conn, _buy("KX-1-A", 50, 0.60, 0.02))
    positions = get_positions(conn, mode="paper")
    assert len(positions) == 1
    pos = positions[0]
    assert pos.contracts == 150
    # cost = (100*0.50 + 0.02) + (50*0.60 + 0.02) = 50.02 + 30.02 = 80.04
    assert pos.cost == pytest.approx(80.04)
    assert position_contracts(conn, mode="paper", ticker="KX-1-A", side="yes") == 150
    assert total_exposure(conn, mode="paper") == pytest.approx(80.04)


def test_rejected_orders_excluded_from_positions(tmp_path) -> None:
    conn = connect(tmp_path / "t.db")
    record_order(conn, _buy("KX-1-A", 100, 0.50, 0.02))
    rejected = OrderRecord(
        mode="paper",
        ticker="KX-1-A",
        event_ticker="KX-1",
        side="yes",
        action="buy",
        count=0,
        price=0.50,
        fee=0.0,
        status="rejected",
        reason="caps reached",
    )
    record_order(conn, rejected)
    assert position_contracts(conn, mode="paper", ticker="KX-1-A", side="yes") == 100
    assert len(get_orders(conn, mode="paper")) == 2
    assert len(get_orders(conn, mode="paper", status="filled")) == 1
