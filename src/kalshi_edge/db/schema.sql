-- Kalshi Edge local store: market snapshots + computed signals.
-- Slices 2-3 add: orders, positions, settlements.

CREATE TABLE IF NOT EXISTS market_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    event_ticker  TEXT,
    yes_bid       REAL,
    yes_ask       REAL,
    no_bid        REAL,
    no_ask        REAL,
    last_price    REAL,
    volume        REAL,
    open_interest REAL,
    spread        REAL
);
CREATE INDEX IF NOT EXISTS idx_snap_ticker_ts ON market_snapshots (ticker, ts);

CREATE TABLE IF NOT EXISTS signals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    p_market            REAL,
    p_fair              REAL,
    confidence          REAL,
    n_books             INTEGER,
    side                TEXT,
    price               REAL,
    edge                REAL,
    ev_net              REAL,
    fee                 REAL,
    kelly_fraction      REAL,
    suggested_contracts INTEGER,
    source              TEXT
);
CREATE INDEX IF NOT EXISTS idx_sig_ticker_ts ON signals (ticker, ts);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    mode            TEXT NOT NULL,   -- paper | demo | live
    ticker          TEXT NOT NULL,
    event_ticker    TEXT,
    side            TEXT NOT NULL,   -- yes | no
    action          TEXT NOT NULL,   -- buy | sell
    count           INTEGER NOT NULL,
    price           REAL NOT NULL,
    fee             REAL NOT NULL,
    status          TEXT NOT NULL,   -- filled | rejected | pending | canceled
    -- count = contracts REQUESTED; filled_count = contracts actually confirmed filled.
    -- paper fills immediately (filled_count = count); demo/live start pending at 0 and
    -- are reconciled against Kalshi's order endpoint (see execution/reconcile.py).
    filled_count    INTEGER NOT NULL DEFAULT 0,
    reason          TEXT,
    p_fair          REAL,
    p_market        REAL,
    ev_net          REAL,
    kalshi_order_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_ticker_ts ON orders (ticker, ts);

CREATE TABLE IF NOT EXISTS settlements (
    ticker       TEXT PRIMARY KEY,
    event_ticker TEXT,
    result       TEXT,           -- yes | no
    settled_ts   TEXT,
    last_price   REAL
);
