# Hardening checklist — before live real-money trading

Two independent review passes (math-correctness + execution red-team) ran on the
money-critical code. The **CRITICAL/HIGH** findings are fixed and tested in the
codebase. The items below are the **MEDIUM/LOW** findings, deferred deliberately —
none affect paper-mode correctness, but each must be addressed before live capital.

## Fixed (in code + tests)
- **Engine-level live gate** — `risk.check` now refuses live unless `LIVE_ENABLED`
  *and* a `live_gate_passed` computed by the engine from the real consistency gate
  (was: gated only by the env flag). Test: `test_live_refused_unless_gate_passes`.
- **Demo/live host guard** — demo requires `KALSHI_ENV=demo`, live requires `prod`,
  so a "demo" order can't hit production. Test: `test_demo_requires_demo_env`.
- **Conservative fees in EV** — ranking uses the cent-rounded per-contract fee, so
  thin bets the smooth fee would falsely rank +EV are rejected.
- **Profit-significance gate** — added a per-trade PnL t-stat (≥1.64) so a lucky
  positive ROI on a small sample can't arm live.
- **Calibration by bin-mean** (not midpoint) to de-bias calibration error.
- **Per-game exposure cap** — limits total spend per event (conservative).
- **Daily-loss cap wired** — realized daily PnL computed from settled trades and enforced
  in `submit` (was inert / always 0). Test: `test_daily_loss_cap_blocks_after_settled_loss`.
- **Devig input validation** — `american_to_prob(0)` and non-positive raw probs now raise.
- **WAL + busy timeout** on the SQLite connection (partial mitigation of the write race).

## Remaining before live (deferred)
1. **Order/fill reconciliation (was: records "filled" on acceptance).** demo/live
   currently mark an accepted order "filled" with the *requested* count. Poll
   Kalshi's order/fills endpoint, record `pending` until confirmed, and store the
   *actual* filled count. Until then, live exposure caps see optimistic state.
2. **Count resting/pending orders toward exposure.** Exposure aggregates filter
   `status='filled'`; a resting limit order won't count, so caps could oversize.
3. ~~Full transaction around read→check→insert~~ — **DONE.** `engine.submit` wraps
   read+gate+record in `BEGIN IMMEDIATE` on an autocommit connection (`record_order`
   defers its commit); the wrapper commits once / rolls back on error, so two concurrent
   passes serialize on the write lock. Test: `test_submit_commit_persists_across_connections`.
   (Note: demo/live hold the lock across the order network call — fine for a single-user
   tool; the reserve-pending pattern in #1 supersedes this when live trading is built.)
4. ~~Wire `daily_pnl` from the ledger~~ — **DONE** (realized daily PnL computed in `submit`).
5. **Reserve the order's own fee in cap math.** `room_*` divides by price without
   reserving the new fee, and the `+1e-9` floor can oversize by ~1 contract. Size
   so `n*price + order_fee(n) + committed <= cap`.
6. **Correlation-aware event netting.** The per-event cap is a gross dollar-spend
   ceiling; it does not recognize that NYK-yes and SAS-no are the same directional
   bet, nor credit hedges. Map opposite sides to a common per-outcome notional.
7. **Verify Kalshi's order-body schema** (`client.create_order`) against the live
   API before the first real order; do a credentialed dry-run on the demo sandbox.
8. ~~Input validation in devig~~ — **DONE** (`american_to_prob(0)` and non-positive raw probs raise).
9. **Live kill switch as a real-time abort** — currently read from `Settings` at
   construction; back it with a polled file/row so it halts a running loop instantly.
