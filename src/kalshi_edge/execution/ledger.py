"""SQLite order ledger + position aggregation (shares the store from ``storage``)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ..model.fees import order_fee


@dataclass(frozen=True)
class OrderRecord:
    mode: str
    ticker: str
    event_ticker: str | None
    side: str  # yes | no
    action: str  # buy | sell
    count: int
    price: float
    fee: float
    status: str  # filled | rejected | pending | canceled
    # Contracts actually confirmed filled. paper sets this to `count` on the spot;
    # demo/live start at 0 (pending) until reconciliation confirms real fills.
    filled_count: int = 0
    reason: str | None = None
    p_fair: float | None = None
    p_market: float | None = None
    ev_net: float | None = None
    kalshi_order_id: str | None = None

    def __post_init__(self) -> None:
        # A record marked 'filled' with no explicit filled_count means fully filled.
        # Without this an omitted argument would silently mean a ZERO fill, which
        # reads as "this order never happened" to both the caps and trade grading --
        # the kind of default that loses money quietly. Partial fills must state
        # their count; 'pending' and 'rejected' legitimately stay at 0.
        if self.status == "filled" and self.filled_count == 0 and self.count > 0:
            object.__setattr__(self, "filled_count", self.count)


@dataclass(frozen=True)
class Position:
    ticker: str
    side: str
    contracts: int
    cost: float  # total $ committed including fees
    avg_price: float


def record_order(conn: sqlite3.Connection, rec: OrderRecord, *, commit: bool = True) -> int:
    cur = conn.execute(
        """INSERT INTO orders
           (ts, mode, ticker, event_ticker, side, action, count, price, fee,
            status, filled_count, reason, p_fair, p_market, ev_net, kalshi_order_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(UTC).isoformat(),
            rec.mode,
            rec.ticker,
            rec.event_ticker,
            rec.side,
            rec.action,
            rec.count,
            rec.price,
            rec.fee,
            rec.status,
            rec.filled_count,
            rec.reason,
            rec.p_fair,
            rec.p_market,
            rec.ev_net,
            rec.kalshi_order_id,
        ),
    )
    if commit:
        conn.commit()
    return int(cur.lastrowid or 0)


def get_orders(
    conn: sqlite3.Connection, *, mode: str | None = None, status: str | None = None
) -> list[sqlite3.Row]:
    query = "SELECT * FROM orders WHERE 1=1"
    params: list[object] = []
    if mode:
        query += " AND mode = ?"
        params.append(mode)
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY ts DESC, id DESC"
    return conn.execute(query, params).fetchall()


# Two different notions of "how many contracts do we have", deliberately kept apart:
#
#   _FILLED  -- contracts actually confirmed filled. Drives displayed positions and
#               (via settlement.py) trade grading. Only real fills may be graded.
#   _HELD    -- contracts committed: confirmed fills PLUS the full requested size of
#               any still-resting order. Drives the risk caps, because a resting limit
#               order can fill at any moment and must not be invisible to sizing.
_FILLED = "SUM(CASE WHEN action='buy' THEN filled_count ELSE -filled_count END)"
_HELD = (
    "SUM(CASE WHEN action='buy' "
    "THEN (CASE WHEN status='pending' THEN count ELSE filled_count END) "
    "ELSE -(CASE WHEN status='pending' THEN count ELSE filled_count END) END)"
)
_FILLED_COST = (
    "SUM(CASE WHEN action='buy' THEN filled_count*price + fee ELSE -(filled_count*price) END)"
)
_HELD_COST = (
    "SUM(CASE WHEN action='buy' "
    "THEN (CASE WHEN status='pending' THEN count ELSE filled_count END)*price + fee "
    "ELSE -((CASE WHEN status='pending' THEN count ELSE filled_count END)*price) END)"
)
# Statuses that tie up capital. 'rejected' and 'canceled' never do.
# Rendered to a SQL list once rather than interpolating the tuple directly: a
# one-element tuple would format as ('filled',) and the trailing comma is a syntax
# error, so the obvious shortcut breaks the day someone shortens this list.
_OPEN_STATUSES = ("filled", "pending")
_OPEN_SQL = "(" + ", ".join(f"'{s}'" for s in _OPEN_STATUSES) + ")"


def get_positions(conn: sqlite3.Connection, *, mode: str) -> list[Position]:
    """Confirmed holdings, for display. Pending orders are NOT positions yet."""
    rows = conn.execute(
        f"""SELECT ticker, side, {_FILLED} AS contracts, {_FILLED_COST} AS cost
            FROM orders WHERE mode = ? AND status = 'filled'
            GROUP BY ticker, side HAVING {_FILLED} > 0""",
        (mode,),
    ).fetchall()
    out: list[Position] = []
    for r in rows:
        contracts = int(r["contracts"])
        cost = float(r["cost"])
        out.append(
            Position(
                ticker=r["ticker"],
                side=r["side"],
                contracts=contracts,
                cost=round(cost, 4),
                avg_price=round(cost / contracts, 4) if contracts else 0.0,
            )
        )
    return out


def position_contracts(conn: sqlite3.Connection, *, mode: str, ticker: str, side: str) -> int:
    """Committed contracts on one market side -- fills plus resting orders."""
    row = conn.execute(
        f"SELECT COALESCE({_HELD}, 0) AS c FROM orders "
        f"WHERE mode = ? AND status IN {_OPEN_SQL} AND ticker = ? AND side = ?",
        (mode, ticker, side),
    ).fetchone()
    return int(row["c"])


def total_exposure(conn: sqlite3.Connection, *, mode: str) -> float:
    """Total $ committed across every market -- fills plus resting orders."""
    row = conn.execute(
        f"SELECT COALESCE({_HELD_COST}, 0.0) AS c FROM orders "
        f"WHERE mode = ? AND status IN {_OPEN_SQL}",
        (mode,),
    ).fetchone()
    return round(float(row["c"]), 4)


def event_exposure(conn: sqlite3.Connection, *, mode: str, event_ticker: str | None) -> float:
    """Gross $ committed across one game -- fills plus resting orders.

    Direction-blind: see ``event_worst_case_exposure`` for the netted view the risk
    gate actually uses. Kept because gross spend is still the right number to show a
    human, and it is the conservative fallback when an event's shape is unknown.
    """
    if not event_ticker:
        return 0.0
    row = conn.execute(
        f"SELECT COALESCE(SUM((CASE WHEN status='pending' THEN count ELSE filled_count END)"
        f"*price + fee), 0.0) AS c FROM orders "
        f"WHERE mode = ? AND status IN {_OPEN_SQL} AND action = 'buy' AND event_ticker = ?",
        (mode, event_ticker),
    ).fetchone()
    return round(float(row["c"]), 4)


def market_subject(ticker: str) -> str:
    """The outcome a market pays out on, from its ticker.

    Kalshi game tickers are ``<SERIES>-<DATE><AWAY><HOME>-<TEAM>``: the suffix names
    the team, and the market resolves YES iff that team wins. Verified against real
    settled data -- event ``KXNBAGAME-26MAY30SASOKC`` holds exactly ``-SAS`` (yes) and
    ``-OKC`` (no). A ticker without a suffix falls back to itself, which keeps it a
    distinct outcome and so stays conservative.
    """
    return ticker.rsplit("-", 1)[-1] if "-" in ticker else ticker


def _event_legs(
    conn: sqlite3.Connection, mode: str, event_ticker: str
) -> list[tuple[str, str, int, float]]:
    """(subject, side, contracts, cost) for every committed leg on one game."""
    rows = conn.execute(
        f"""SELECT ticker, side,
                   SUM(CASE WHEN status='pending' THEN count ELSE filled_count END) AS n,
                   SUM((CASE WHEN status='pending' THEN count ELSE filled_count END)*price + fee)
                       AS cost
            FROM orders
            WHERE mode = ? AND status IN {_OPEN_SQL} AND action = 'buy'
                  AND event_ticker = ?
            GROUP BY ticker, side""",
        (mode, event_ticker),
    ).fetchall()
    return [
        (market_subject(r["ticker"]), r["side"], int(r["n"] or 0), float(r["cost"] or 0.0))
        for r in rows
    ]


def event_outcome_universe(conn: sqlite3.Connection, event_ticker: str) -> set[str]:
    """Every outcome this game can resolve to, from observed MARKET data.

    Deliberately read from ``market_snapshots`` rather than from our own orders: the
    question is what the world can do, not what we happen to hold. Deriving it from
    our own positions would be circular -- holding only SAS would "prove" SAS must
    win. An empty result means we cannot prove the outcome set, and the caller stays
    conservative.
    """
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM market_snapshots WHERE event_ticker = ?",
        (event_ticker,),
    ).fetchall()
    return {market_subject(r["ticker"]) for r in rows}


def _worst_case(legs: list[tuple[str, str, int, float]], universe: set[str] | None = None) -> float:
    """Largest loss this book can take across every way the game can resolve.

    A YES leg pays $1 per contract iff its subject wins; a NO leg pays iff its subject
    does not.

    The scenario set matters as much as the arithmetic. Normally it includes ``None``
    -- "an outcome we hold no market on wins" -- which is what keeps a one-sided book
    honest. But when ``universe`` shows the game's outcomes are fully covered by what
    we hold, that scenario is IMPOSSIBLE (somebody has to win), and leaving it in makes
    it the binding constraint: it prices a hedge as though both legs could lose at
    once, which erases exactly the netting this function exists to provide.
    """
    if not legs:
        return 0.0
    total_cost = sum(cost for _, _, _, cost in legs)
    subjects = {subj for subj, _, _, _ in legs}
    exhaustive = bool(universe) and len(universe) >= 2 and universe <= subjects
    scenarios: tuple[str | None, ...] = tuple(subjects) if exhaustive else (*subjects, None)
    worst = max(
        total_cost
        - sum(
            n
            for subj, side, n, _ in legs
            if (side == "yes" and subj == winner) or (side == "no" and subj != winner)
        )
        for winner in scenarios
    )
    return max(0.0, worst)


def event_worst_case_exposure(
    conn: sqlite3.Connection, *, mode: str, event_ticker: str | None
) -> float:
    """Worst-case $ loss on one game across every way it can resolve.

    The per-event cap used to sum gross spend, which double-counts a directional view
    expressed twice (buying NYK-yes and SAS-no is ONE bet on NYK) and refuses to credit
    a genuine hedge (NYK-yes plus SAS-yes cannot both lose). Both are fixed by pricing
    the book against each outcome instead of adding up receipts.

    Never more permissive than gross spend for a one-sided book (nothing pays out in
    the losing scenario, so worst-case loss == cost); strictly better when hedged.
    """
    if not event_ticker:
        return 0.0
    return round(
        _worst_case(
            _event_legs(conn, mode, event_ticker),
            event_outcome_universe(conn, event_ticker),
        ),
        4,
    )


def event_room_contracts(
    conn: sqlite3.Connection,
    *,
    mode: str,
    event_ticker: str | None,
    ticker: str,
    side: str,
    price: float,
    cap_dollars: float,
    fee_multiplier: float,
    limit: int,
) -> int:
    """Largest size for THIS order that keeps the game's worst-case loss within cap.

    Sizing has to price the candidate order INTO the book rather than subtracting a
    scalar from the cap, because the whole point of netting is that the same dollar
    of spend means different risk depending on direction: a leg that hedges the
    existing book barely moves the worst case, while one that doubles down moves it
    dollar for dollar. Subtracting a scalar gives both the identical room and quietly
    throws the netting away.

    Worst-case loss is a max of per-scenario lines in ``n`` and therefore convex, and
    the book already sits inside the cap, so the feasible sizes form a contiguous run
    from zero -- which makes a binary search sound.
    """
    if not event_ticker or limit <= 0:
        return max(0, limit)
    legs = _event_legs(conn, mode, event_ticker)
    subject = market_subject(ticker)
    universe = event_outcome_universe(conn, event_ticker)

    def worst_with(n: int) -> float:
        cost = n * price + order_fee(n, price, multiplier=fee_multiplier)
        return _worst_case([*legs, (subject, side, n, cost)], universe)

    if worst_with(limit) <= cap_dollars:
        return limit
    lo, hi = 0, limit  # lo always feasible, hi always infeasible
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if worst_with(mid) <= cap_dollars:
            lo = mid
        else:
            hi = mid
    return lo


def pending_orders(conn: sqlite3.Connection, *, mode: str) -> list[sqlite3.Row]:
    """Orders accepted by Kalshi but not yet confirmed filled."""
    return conn.execute(
        "SELECT * FROM orders WHERE mode = ? AND status = 'pending' "
        "AND kalshi_order_id IS NOT NULL AND kalshi_order_id != '' ORDER BY id",
        (mode,),
    ).fetchall()


def update_order_fill(
    conn: sqlite3.Connection,
    order_id: int,
    *,
    filled_count: int,
    status: str,
    reason: str | None = None,
    fee: float | None = None,
    commit: bool = True,
) -> None:
    """Record the confirmed fill for one order, and optionally restate its fee.

    The fee is charged on contracts that trade, so an order booked for 100 and filled
    for 10 must not keep the 100-contract fee: that inflates cost everywhere it is
    read -- overstating losses in grading and in the daily-loss cap.
    """
    sets = ["filled_count = ?", "status = ?"]
    params: list[object] = [filled_count, status]
    if reason is not None:
        sets.append("reason = ?")
        params.append(reason)
    if fee is not None:
        sets.append("fee = ?")
        params.append(fee)
    params.append(order_id)
    conn.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id = ?", params)
    if commit:
        conn.commit()
