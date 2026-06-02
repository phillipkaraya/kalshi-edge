"""Grade settled paper trades and decide whether live trading may arm.

Metrics: hit rate, ROI (net of fees), and two Brier scores -- one for our model's
fair value and one for the market's implied probability -- plus a calibration
curve. The live gate is deliberately strict: enough settled trades, positive ROI,
the model beating the market on Brier, and low calibration error. Passing the gate
is NECESSARY but not sufficient: a human still explicitly arms live (Slice 5).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from math import sqrt
from statistics import pstdev


@dataclass(frozen=True)
class SettledTrade:
    ticker: str
    side: str  # yes | no (the side we bet)
    price: float  # entry price paid for that side
    count: int
    fee: float
    p_fair: float  # our fair probability of YES at entry
    p_market: float  # market-implied probability of YES at entry
    outcome_yes: int  # 1 if the market resolved YES, else 0

    @property
    def won(self) -> bool:
        return (self.side == "yes") == (self.outcome_yes == 1)

    @property
    def cost(self) -> float:
        return self.count * self.price + self.fee

    @property
    def payoff(self) -> float:
        return float(self.count) if self.won else 0.0

    @property
    def pnl(self) -> float:
        return self.payoff - self.cost


@dataclass(frozen=True)
class ConsistencyMetrics:
    n: int
    hit_rate: float
    roi: float
    total_pnl: float
    total_cost: float
    brier_model: float | None
    brier_market: float | None
    model_beats_market: bool
    calibration: list[tuple[float, float, int]] = field(default_factory=list)
    calibration_error: float = 0.0
    pnl_tstat: float = 0.0  # t-stat of per-trade PnL vs 0 (profit-significance for the gate)


def compute_metrics(trades: list[SettledTrade], *, bins: int = 10) -> ConsistencyMetrics:
    n = len(trades)
    if n == 0:
        return ConsistencyMetrics(0, 0.0, 0.0, 0.0, 0.0, None, None, False)

    wins = sum(1 for t in trades if t.won)
    total_cost = sum(t.cost for t in trades)
    total_pnl = sum(t.pnl for t in trades)
    brier_model = sum((t.p_fair - t.outcome_yes) ** 2 for t in trades) / n
    brier_market = sum((t.p_market - t.outcome_yes) ** 2 for t in trades) / n

    # Calibration by p_fair bin, referenced against the MEAN predicted prob in each
    # bin (not the static midpoint) so the error isn't biased when preds cluster.
    buckets: dict[int, list[tuple[float, int]]] = {}
    for t in trades:
        b = min(bins - 1, max(0, int(t.p_fair * bins)))
        buckets.setdefault(b, []).append((t.p_fair, t.outcome_yes))
    calibration: list[tuple[float, float, int]] = []
    err_num = 0.0
    for b in sorted(buckets):
        rows = buckets[b]
        ref = sum(p for p, _ in rows) / len(rows)
        observed = sum(y for _, y in rows) / len(rows)
        calibration.append((round(ref, 3), round(observed, 3), len(rows)))
        err_num += abs(ref - observed) * len(rows)

    # Profit-significance: is mean per-trade PnL statistically above 0? (Guards against
    # a lucky positive ROI on a small sample passing the gate.)
    pnls = [t.pnl for t in trades]
    mean_pnl = total_pnl / n
    std_pnl = pstdev(pnls) if n > 1 else 0.0
    pnl_tstat = mean_pnl / (std_pnl / sqrt(n)) if std_pnl > 0 else (99.0 if mean_pnl > 0 else 0.0)

    return ConsistencyMetrics(
        n=n,
        hit_rate=round(wins / n, 4),
        roi=round(total_pnl / total_cost, 4) if total_cost else 0.0,
        total_pnl=round(total_pnl, 2),
        total_cost=round(total_cost, 2),
        brier_model=round(brier_model, 4),
        brier_market=round(brier_market, 4),
        model_beats_market=brier_model < brier_market,
        calibration=calibration,
        calibration_error=round(err_num / n, 4),
        pnl_tstat=round(pnl_tstat, 3),
    )


@dataclass(frozen=True)
class GateThresholds:
    min_trades: int = 100
    min_roi: float = 0.0
    require_model_beats_market: bool = True
    max_calibration_error: float = 0.10
    min_pnl_tstat: float = 1.64  # realized profit must be statistically > 0 (~95% one-sided)


@dataclass(frozen=True)
class GateStatus:
    passed: bool
    checks: list[tuple[str, bool, str]]


def evaluate_gate(m: ConsistencyMetrics, thresholds: GateThresholds | None = None) -> GateStatus:
    t = thresholds or GateThresholds()
    checks: list[tuple[str, bool, str]] = [
        (f"≥{t.min_trades} settled trades", m.n >= t.min_trades, str(m.n)),
        (f"ROI ≥ {t.min_roi:.0%}", m.roi >= t.min_roi, f"{m.roi:.1%}"),
        (
            "model beats market (Brier)",
            (m.model_beats_market if t.require_model_beats_market else True),
            f"{m.brier_model} vs {m.brier_market}",
        ),
        (
            f"calibration error ≤ {t.max_calibration_error}",
            m.calibration_error <= t.max_calibration_error,
            str(m.calibration_error),
        ),
        (
            f"profit significant (t ≥ {t.min_pnl_tstat})",
            m.pnl_tstat >= t.min_pnl_tstat,
            str(m.pnl_tstat),
        ),
    ]
    return GateStatus(passed=all(ok for _, ok, _ in checks), checks=checks)


def synthetic_settled_trades(n: int = 120, *, seed: int = 7) -> list[SettledTrade]:
    """Deterministic demo data for populating the Consistency page before any real
    paper trades have settled (tickers ``SYN-*``). It is only marginally skilled, so
    on most seeds it does NOT clear the gate -- a faithful demonstration that the gate
    is a real barrier, not a rubber stamp."""
    rng = random.Random(seed)
    out: list[SettledTrade] = []
    for i in range(n):
        true_p = rng.uniform(0.2, 0.8)
        p_market = min(0.99, max(0.01, true_p + rng.uniform(-0.05, 0.05)))
        p_fair = min(0.99, max(0.01, true_p + rng.uniform(-0.02, 0.02)))  # closer to truth
        outcome = 1 if rng.random() < true_p else 0
        side = "yes" if p_fair > p_market else "no"
        price = round(p_market if side == "yes" else 1 - p_market, 2)
        out.append(
            SettledTrade(
                f"SYN-{i}", side, price, 10, 0.01, round(p_fair, 4), round(p_market, 4), outcome
            )
        )
    return out
