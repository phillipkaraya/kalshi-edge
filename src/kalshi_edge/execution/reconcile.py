"""Reconcile accepted orders against Kalshi's real fill state.

demo/live used to record an accepted order as ``filled`` for the full requested
count the instant Kalshi returned 200. Acceptance is not execution: a resting limit
order may fill partially, later, or never. Believing the optimistic number means the
exposure caps size against contracts that do not exist, and the consistency gate
grades trades that never happened.

So the order path now writes ``pending`` with ``filled_count = 0`` and this module
is the only thing that promotes an order to ``filled`` -- with the count Kalshi
actually reports. Paper mode never passes through here; it fills by construction.

One thing this deliberately does NOT do is time a pending order out locally. An order
that has rested for days is still live on Kalshi's book until Kalshi says otherwise,
so quietly dropping it from our own exposure would free cap room that is genuinely
committed and let the next pass oversize -- reintroducing the bug this module exists
to fix, in a harder-to-see form. Stale orders are therefore counted and surfaced for a
human decision; cancelling one is a trading action and stays a deliberate act.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ..kalshi.client import KalshiClient, parse_fill
from ..model.fees import order_fee
from .ledger import pending_orders, update_order_fill


@dataclass(frozen=True)
class ReconcileReport:
    checked: int = 0
    filled: int = 0
    partial: int = 0
    canceled: int = 0
    still_pending: int = 0
    errors: int = 0
    stale: int = 0  # pending far longer than expected -- needs a human decision

    @property
    def changed(self) -> int:
        return self.filled + self.partial + self.canceled


def reconcile_pending(
    conn: sqlite3.Connection,
    client: KalshiClient | None,
    *,
    mode: str,
    fee_multiplier: float = 0.07,
    stale_after_hours: float = 24.0,
) -> ReconcileReport:
    """Poll every pending order for ``mode`` and record its true fill state.

    Safe to run on a schedule: it only ever touches rows already marked pending, and
    a lookup failure leaves the row pending rather than guessing.
    """
    if client is None or not client.authenticated or mode == "paper":
        return ReconcileReport()

    checked = filled = partial = canceled = still_pending = errors = stale = 0
    now = datetime.now(UTC)
    for row in pending_orders(conn, mode=mode):
        checked += 1
        order_id = str(row["kalshi_order_id"])
        try:
            payload = client.get_order(order_id)
        except Exception:  # network/API failure -- leave it pending, try again later
            errors += 1
            continue

        n_filled, status = parse_fill(payload)
        requested = int(row["count"])
        n_filled = min(n_filled, requested)  # never record more than we asked for
        # Restate the fee for what actually traded; the row was booked at full size.
        real_fee = order_fee(n_filled, float(row["price"]), multiplier=fee_multiplier)

        if status == "filled" and n_filled > 0:
            update_order_fill(
                conn,
                int(row["id"]),
                filled_count=n_filled,
                status="filled",
                reason=(
                    f"reconciled: filled {n_filled}/{requested}"
                    if n_filled < requested
                    else f"reconciled: filled {n_filled}"
                ),
                fee=real_fee,
                commit=False,
            )
            if n_filled < requested:
                partial += 1
            else:
                filled += 1
        elif status == "canceled":
            # Canceled with a partial fill keeps the contracts that did trade; the rest
            # stops consuming cap room. Zero-fill cancels leave no position at all.
            update_order_fill(
                conn,
                int(row["id"]),
                filled_count=n_filled,
                status="filled" if n_filled > 0 else "canceled",
                reason=f"reconciled: canceled after {n_filled}/{requested}",
                fee=real_fee,
                commit=False,
            )
            canceled += 1
        else:
            still_pending += 1
            if _age_hours(row["ts"], now) > stale_after_hours:
                stale += 1
    conn.commit()
    return ReconcileReport(checked, filled, partial, canceled, still_pending, errors, stale)


def _age_hours(ts: str, now: datetime) -> float:
    """Hours since an order row was written; 0 if the timestamp is unreadable."""
    try:
        written = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return 0.0
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    return (now - written).total_seconds() / 3600.0
