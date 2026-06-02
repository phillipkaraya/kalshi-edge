"""Edge evaluation: side selection, fee-aware EV, and Kelly sizing."""

from __future__ import annotations

import pytest

from kalshi_edge.model.edge import evaluate_edge


def test_picks_yes_when_underpriced() -> None:
    r = evaluate_edge(0.60, yes_ask=0.50, no_ask=0.51, bankroll=1000, min_ev=0.0)
    assert r.side == "yes"
    assert r.p_win == pytest.approx(0.60)
    # EV net = 0.60 - 0.50 - order_fee(1, 0.50)=0.02 -> 0.08 (conservative rounded fee)
    assert r.ev_net == pytest.approx(0.08, abs=1e-4)
    assert r.suggested_contracts > 0


def test_picks_no_when_yes_overpriced() -> None:
    r = evaluate_edge(0.40, yes_ask=0.50, no_ask=0.50, bankroll=1000, min_ev=0.0)
    assert r.side == "no"
    assert r.p_win == pytest.approx(0.60)  # NO wins with prob 1 - p_fair


def test_fees_can_erase_a_thin_edge() -> None:
    # 1c gross edge at 50c is less than the 1.75c fee -> no trade.
    r = evaluate_edge(0.51, yes_ask=0.50, no_ask=0.50, bankroll=1000, min_ev=0.0)
    assert r.side == "none"
    assert r.suggested_contracts == 0


def test_quarter_kelly_and_cap() -> None:
    # f* = 0.20; quarter-Kelly = 0.05; cap = 0.05 -> 0.05 of $1000 / $0.50 = 100 contracts.
    r = evaluate_edge(
        0.60,
        yes_ask=0.50,
        no_ask=0.99,
        bankroll=1000,
        kelly_frac=0.25,
        max_fraction=0.05,
        min_ev=0.0,
    )
    assert r.kelly_fraction == pytest.approx(0.05)
    assert r.suggested_contracts == 100
