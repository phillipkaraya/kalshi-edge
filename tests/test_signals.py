"""Momentum, arbitrage, and injury-nudge signals."""

from __future__ import annotations

from kalshi_edge.data.balldontlie import injury_nudge, status_delta
from kalshi_edge.kalshi.models import Market
from kalshi_edge.model.arbitrage import event_overround, two_sided_lock
from kalshi_edge.model.momentum import momentum_signal, order_book_imbalance, price_move


def test_order_book_imbalance() -> None:
    assert order_book_imbalance(300, 100) == 0.5  # (300-100)/400
    assert order_book_imbalance(0, 0) is None
    assert order_book_imbalance(None, 10) is None


def test_momentum_signal_from_market() -> None:
    m = Market.model_validate(
        {
            "ticker": "A-B-C",
            "event_ticker": "A-B",
            "status": "active",
            "last_price_dollars": "0.55",
            "previous_price_dollars": "0.50",
            "yes_bid_size_fp": 300,
            "yes_ask_size_fp": 100,
        }
    )
    sig = momentum_signal(m)
    assert price_move(0.55, 0.50) == 0.05
    assert sig.label == "up"
    assert sig.imbalance == 0.5
    assert sig.price_move == 0.05


def test_two_sided_lock() -> None:
    arb = two_sided_lock(0.45, 0.45)  # 0.90 + small fees < 1.0
    assert arb.is_arb
    assert arb.profit_per_contract > 0
    assert not two_sided_lock(0.52, 0.52).is_arb  # 1.04 > 1.0
    assert not two_sided_lock(None, 0.4).is_arb


def test_event_overround() -> None:
    assert event_overround([0.55, 0.50]) == 0.05
    assert event_overround([0.48, 0.49]) == -0.03


def test_injury_nudge_and_status_delta() -> None:
    assert injury_nudge(0.60, key_player_out_delta=-0.06) == 0.54
    assert injury_nudge(0.02, key_player_out_delta=-0.10) == 0.01  # clamped low
    assert status_delta("Out") > status_delta("Questionable") > 0.0
    assert status_delta("nonsense") == 0.0
