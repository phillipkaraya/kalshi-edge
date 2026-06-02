"""Go-live preflight: every condition that must hold before real money can trade.

This is the final safety interlock layered on top of the per-order risk gate. It
combines the Slice 3 consistency gate with operational prerequisites (mode, the
explicit LIVE_ENABLED flag, credentials, kill switch). All must be green; the app
never self-arms -- a human sets LIVE_ENABLED.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..backtest.consistency import ConsistencyMetrics, GateThresholds, evaluate_gate
from ..config import Settings


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class PreflightResult:
    go: bool
    checks: list[PreflightCheck]


def has_credentials(settings: Settings) -> bool:
    return bool(settings.kalshi_key_id and settings.kalshi_private_key_path)


def live_preflight(
    settings: Settings,
    metrics: ConsistencyMetrics,
    *,
    thresholds: GateThresholds | None = None,
    has_creds: bool | None = None,
) -> PreflightResult:
    gate = evaluate_gate(metrics, thresholds or GateThresholds())
    creds = has_credentials(settings) if has_creds is None else has_creds
    checks = [
        PreflightCheck(
            "execution mode is 'live'", settings.execution_mode == "live", settings.execution_mode
        ),
        PreflightCheck("LIVE_ENABLED set", settings.live_enabled, str(settings.live_enabled)),
        PreflightCheck("kill switch off", not settings.kill_switch, str(settings.kill_switch)),
        PreflightCheck(
            "Kalshi credentials present", creds, "yes" if creds else "missing key id / pem"
        ),
        PreflightCheck("consistency gate passed", gate.passed, "pass" if gate.passed else "fail"),
    ]
    return PreflightResult(go=all(c.ok for c in checks), checks=checks)
