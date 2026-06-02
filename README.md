# Kalshi Edge

NBA prediction-market decision engine for **Kalshi**. It produces an independent
probability for every NBA contract (by devigging the sportsbook consensus),
compares it to Kalshi's price, ranks the disagreements ("edges") by expected
value, paper-trades them with full logging, and — only once a measured
**consistency gate** passes — can flip to gated live auto-trading.

> **Status:** Slice 0 — live read-only Edge Board (no credentials needed).

## Quickstart

```bash
uv sync
uv run streamlit run src/kalshi_edge/ui/app.py
```

Kalshi market data is public, so the Edge Board works with no API keys. Copy
`.env.example` to `.env` when you reach the data/trading slices.

## Roadmap (vertical slices)

| Slice | What | Needs keys? |
|------|------|-------------|
| 0 | Scaffold + live NBA Edge Board (market-implied prob + liquidity) | No |
| 1 | Devig consensus → `p_fair`, edge, EV (net fees), Kelly sizing | Odds/BALLDONTLIE (free) |
| 2 | Paper execution + SQLite ledger (paper/demo) | Kalshi (demo) |
| 3 | Consistency harness (ROI/Brier/calibration) + live gate | — |
| 4 | Momentum, injury/news, arbitrage signals | — |
| 5 | Gated live auto-trading + kill switch | Kalshi (prod, funded) |

See `CLAUDE.md` for architecture and conventions.
