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

**Hardening pass 3 (2026-08-19)** closed items 1, 2, 5, 6 and 9 in code + tests, and
took 7 as far as it can go without touching a real account. 148 tests pass; ruff + ty
clean. What each one actually changed:

1. ~~Order/fill reconciliation~~ — **DONE.** `count` (requested) and `filled_count`
   (confirmed) are now separate columns. demo/live record **`pending` with a zero
   fill** on acceptance; only `execution/reconcile.py` promotes a row to `filled`, with
   the count Kalshi reports. A lookup failure leaves the row pending rather than
   guessing, a fill is never recorded above the requested size, and a cancel keeps only
   the contracts that actually traded. Wired into the scheduled pass, which reconciles
   *before* sizing anything new. Paper is unaffected (it fills by construction).
   Tests: `test_reconcile_records_the_real_partial_fill`,
   `test_reconcile_leaves_the_row_pending_when_kalshi_cannot_be_reached`.
2. ~~Count resting/pending orders toward exposure~~ — **DONE.** The cap aggregates
   count `filled` **and** `pending` (a resting order commits capital and can fill at
   any moment); displayed positions and trade grading still count confirmed fills only.
   Test: `test_resting_order_consumes_cap_room`, `test_pending_order_still_consumes_cap_room`.
3. ~~Full transaction around read→check→insert~~ — **DONE** (pass 2).
4. ~~Wire `daily_pnl` from the ledger~~ — **DONE** (pass 2).
5. ~~Reserve the order's own fee in cap math~~ — **DONE.** Sizing solves for the
   largest `n` with `n*price + order_fee(n) + committed <= cap`
   (`fees.max_contracts_within_budget`), and the `+1e-9` slack is gone. This was not
   theoretical: on the default $50 market cap the old code allowed 100 contracts at
   $0.50 that cost **$51.75** all-in — a $1.75 breach of the cap being enforced.
   Tests: `test_sizing_never_exceeds_its_budget_once_the_fee_is_paid` (parametrised
   across prices and budgets), `test_no_cap_is_ever_breached_once_fees_are_paid`.
6. ~~Correlation-aware event netting~~ — **DONE.** The per-game cap is now a
   **worst-case loss** across every way the game can resolve, not gross spend. A YES
   leg pays iff its subject wins, a NO leg iff it does not, and the subject is read off
   the ticker suffix (`…-SAS`), verified against real settled data. Sizing prices the
   candidate order *into* the book rather than subtracting a scalar — that distinction
   is the whole feature, since a scalar gives a hedge and a doubling-down bet identical
   room. Measured on the $60 default cap: same-direction second leg squeezed 96 → 19
   contracts, while a true hedge clears its full 96 and drops worst-case exposure from
   $49.68 to $3.36. Never more permissive than gross spend for a one-sided book.
   **Safety note:** the "some other outcome wins" scenario is only dropped when
   `market_snapshots` proves the game's outcomes are fully covered; with no market data
   the conservative scenario stays in and no hedge credit is given.
   Tests: `test_opposite_sides_of_one_game_are_one_bet`, `test_a_real_hedge_is_credited`,
   `test_one_sided_book_is_never_cheaper_than_gross_spend`,
   `test_unknown_outcome_universe_stays_conservative`.
7. **Verify Kalshi's order-body schema — PARTIALLY DONE, still blocks live.**
   The body was checked against Kalshi's published docs and **was wrong**: it used the
   classic `/portfolio/orders` shape (`action`, `side: yes/no`, `type`, integer-cent
   `yes_price`/`no_price`), and that endpoint was slated for deprecation **no earlier
   than 2026-05-06** — already past. The current V2 schema quotes a single YES book:
   `side` is `bid`/`ask`, `count` and `price` are fixed-point decimal *strings* in
   dollars, and `time_in_force` + `self_trade_prevention_type` are required.
   The payload now comes from a pure, unit-tested `build_order_body()` defaulting to
   V2, with `legacy` available via `KALSHI_ORDER_SCHEMA`. Note the subtle mapping:
   buying NO at $0.42 is expressed as **selling YES at $0.58** (`side: ask`), so an
   error here would place a real order on the wrong side at the wrong price — hence the
   dedicated test.
   **STILL REQUIRED, and this is Phil's step:** a credentialed dry-run on the demo
   sandbox to confirm which schema the account actually accepts. Nothing in this
   codebase has ever sent an order to a real Kalshi account, and that cannot be
   verified without placing one.
8. ~~Input validation in devig~~ — **DONE** (pass 1).
9. ~~Live kill switch as a real-time abort~~ — **DONE.** `RiskManager.kill_engaged()`
   polls `KALSHI_KILL_SWITCH_FILE` (default `data/KILL`) before **every** order, so a
   running loop halts the moment the file appears rather than at the next restart. The
   env flag remains as a start-up setting. A filesystem error counts as engaged: if we
   cannot tell whether we were told to stop, we stop.
   Halt a live session with: `touch data/KILL`
   Test: `test_kill_switch_file_halts_a_running_session`.

## Found while closing the backlog (2026-08-21)
- **Grading and the daily-loss cap read `count` instead of `filled_count`** — FIXED.
  Splitting requested from confirmed left both consumers on the requested size, so a
  partial fill would have been graded, and charged against the stop, at full size.
  Latent in paper (where they are equal); live-only, and exactly the bug #1 exists to
  prevent. Regression tests fail against the old queries.
- **The recorded fee was never restated after a partial fill** — FIXED. An order booked
  for 100 that filled 10 kept a 100-contract fee, overstating cost in grading and the
  daily-loss cap. Reconciliation now recomputes it.
- **The Positions page counted resting orders as rejections** — FIXED. `pending` is
  reported separately, so an accepted order no longer reads to the user as a failure.
- **Stale pending orders** — DETECTED, deliberately NOT auto-expired. A long-resting
  order is still live on Kalshi's book, so dropping it locally would free cap room that
  is genuinely committed and let the next pass oversize. They are counted and surfaced;
  cancelling one is a trading action and stays a deliberate human act. *Open question
  for Phil: what policy should apply to an order resting more than a day?*
- **🔴 The market matcher had gone blind, and this was the big one** — FIXED. Kalshi
  titles regular-season game markets **"San Antonio wins"**, but `parse_kalshi_game`
  only understood the playoff-style **"X at Y Winner?"** grammar, so every market
  parsed to `None` and the scheduled pass produced **zero signals**. It would have run
  all season logging nothing, and the ≥100-trade consistency gate could never have
  filled. Parsing now falls back to the ticker (`KXNBAGAME-26OCT20OKCSAS-SAS` =
  OKC at SAS, YES = SAS), which is structured data rather than prose Kalshi can
  restyle. Verified live: **6 of 6 open markets now match and produce signals**, each
  against the correct distinct game.

## Still open before real capital
- **#7's credentialed demo dry-run** (above) — the one item code cannot close.
- Rotate the Kalshi private key: it transited a chat transcript on 2026-06-01 and
  `~/.kalshi/kalshi_key.pem` is unchanged since, so the exposure stands.
- Confirm NBA contracts are tradable in **GA** before any live order.
- The consistency gate still needs ≥100 settled paper trades; it sits at 0 until the
  season resumes (the hourly paper pass accumulates them automatically).
