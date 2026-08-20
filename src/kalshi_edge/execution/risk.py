"""Risk gate: the rules every order must clear before it can be placed.

This is the safety core. ``live`` mode is refused unless BOTH ``live_enabled`` is
set AND the caller passes ``live_gate_passed=True`` -- which the engine computes
from the Slice 3 consistency gate over real settled paper trades, so the gate is
enforced on the order path (not just in the UI). Kill switch and the per-market /
total-exposure / per-event / daily-loss caps apply to all modes, so paper trading
exercises the exact same guardrails.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..model.fees import max_contracts_within_budget


@dataclass(frozen=True)
class RiskConfig:
    min_ev: float
    min_liquidity_spread: float
    max_contracts_per_market: int
    max_position_fraction: float
    max_total_exposure_fraction: float
    max_daily_loss: float
    kill_switch: bool
    live_enabled: bool
    max_event_exposure_fraction: float = 0.06
    fee_multiplier: float = 0.07
    # Polled before every order so a kill lands mid-run, not next restart.
    kill_switch_file: Path | None = None

    @classmethod
    def from_settings(cls, s: Settings) -> RiskConfig:
        return cls(
            min_ev=s.min_ev,
            min_liquidity_spread=s.min_liquidity_spread,
            max_contracts_per_market=s.max_contracts_per_market,
            max_position_fraction=s.max_position_fraction,
            max_total_exposure_fraction=s.max_total_exposure_fraction,
            max_daily_loss=s.max_daily_loss,
            kill_switch=s.kill_switch,
            live_enabled=s.live_enabled,
            max_event_exposure_fraction=s.max_event_exposure_fraction,
            fee_multiplier=s.fee_multiplier,
            kill_switch_file=s.kill_switch_file,
        )


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    max_contracts: int = 0


class RiskManager:
    def __init__(self, config: RiskConfig, bankroll: float) -> None:
        self.config = config
        self.bankroll = bankroll

    def kill_engaged(self) -> bool:
        """True if trading is halted, re-checked on every order.

        The env flag is a start-up setting; the file is the live abort. Polling it
        here (rather than snapshotting at construction) is what makes the switch a
        real-time stop for an already-running loop -- create the file and the very
        next order is refused. A filesystem error is treated as engaged: if we cannot
        tell whether we were told to stop, we stop.
        """
        if self.config.kill_switch:
            return True
        path = self.config.kill_switch_file
        if path is None:
            return False
        try:
            return Path(path).exists()
        except OSError:
            return True

    def check(
        self,
        *,
        mode: str,
        ev_net: float | None,
        spread: float | None,
        requested_contracts: int,
        price: float,
        current_position_contracts: int,
        current_exposure: float,
        daily_pnl: float,
        current_event_exposure: float = 0.0,
        event_room_contracts: int | None = None,
        live_gate_passed: bool = False,
    ) -> RiskDecision:
        c = self.config
        if self.kill_engaged():
            return RiskDecision(False, "kill switch engaged")
        if mode == "live" and not (c.live_enabled and live_gate_passed):
            return RiskDecision(
                False, "live locked: consistency gate or LIVE_ENABLED not satisfied"
            )
        if not (0.0 < price < 1.0):
            return RiskDecision(False, f"price {price} out of range")
        if ev_net is None or ev_net < c.min_ev:
            return RiskDecision(False, f"EV {ev_net} below min {c.min_ev}")
        if spread is not None and spread > c.min_liquidity_spread:
            return RiskDecision(False, f"spread {spread} too wide (> {c.min_liquidity_spread})")
        if daily_pnl <= -c.max_daily_loss:
            return RiskDecision(False, "daily loss limit reached")
        if requested_contracts <= 0:
            return RiskDecision(False, "no contracts requested")

        # Every cap reserves the fee the new order will itself incur, and drops the old
        # `+1e-9` slack -- together those let a position be sized flush to a cap and then
        # breach it the moment the fee landed (HARDENING #5).
        def room(budget: float) -> int:
            return max_contracts_within_budget(budget, price, multiplier=c.fee_multiplier)

        market_cap = min(
            c.max_contracts_per_market,
            room(c.max_position_fraction * self.bankroll),
        )
        room_market = market_cap - current_position_contracts
        room_exposure = room(c.max_total_exposure_fraction * self.bankroll - current_exposure)
        # Per-game cap. The caller can pass `event_room_contracts`, a netting-aware
        # size computed by pricing THIS order into the game's book (see
        # ledger.event_room_contracts) -- that is what lets a hedge keep its room while
        # a doubling-down bet is squeezed. Without it, fall back to the direction-blind
        # scalar, which is conservative but treats both the same.
        room_event = (
            event_room_contracts
            if event_room_contracts is not None
            else room(c.max_event_exposure_fraction * self.bankroll - current_event_exposure)
        )
        allowed = min(requested_contracts, room_market, room_exposure, room_event)
        if allowed <= 0:
            return RiskDecision(False, "position/exposure/event caps reached")
        return RiskDecision(True, "ok", int(allowed))
