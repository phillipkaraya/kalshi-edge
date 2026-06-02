# IntellovateBets

NBA prediction-market decision engine for Kalshi: devig the sportsbook consensus,
find where Kalshi's price disagrees, paper-trade the edge, then auto-trade behind
a measured consistency gate. Live board: https://intellovatebets.streamlit.app

> Product/display name is **IntellovateBets**. The Python package and repo stay `kalshi_edge` / `kalshi-edge` (import paths and directory names are unchanged).

## Stack
- Python 3.12, `uv` + hatchling, `src/` layout
- httpx (HTTP), pydantic + pydantic-settings (models/config), cryptography (RSA-PSS signing)
- Streamlit (dashboard), pandas (tables), SQLite (ledger/backtest), Rich (CLI logs)

## Structure
- `src/kalshi_edge/config.py` -- settings (.env); env (prod/demo) + host switch
- `src/kalshi_edge/kalshi/` -- API client (RSA-PSS auth), Pydantic models, market fetch/filter
- `src/kalshi_edge/data/` -- The Odds API + BALLDONTLIE adapters, devig (Slice 1)
- `src/kalshi_edge/model/` -- p_fair blend, edge/EV/Kelly, momentum, arbitrage (Slice 1+)
- `src/kalshi_edge/execution/` -- paper|demo|live engine, risk gate, ledger (Slice 2+)
- `src/kalshi_edge/backtest/` -- consistency: ROI/Brier/calibration (Slice 3)
- `src/kalshi_edge/ui/` -- Streamlit Edge Board + pages
- `tests/` -- pytest (devig, EV, Kelly, risk gate, signing)

## Commands
- Install: `uv sync`
- Dashboard: `uv run streamlit run src/kalshi_edge/ui/app.py`
- Test: `uv run pytest`
- Lint/format: `uvx ruff check --fix && uvx ruff format`   (type-check: `uvx ty check src`)

## Known Issues
- Slice 0: live trading not wired yet; market reads are public/no-auth.

## Rules
- Kalshi market reads are PUBLIC (no key). Auth (RSA-PSS) only for portfolio/orders.
- Prices arrive as dollar-strings (`*_dollars`) and `_fp` floats -- always parse via the
  Pydantic models in `kalshi/models.py`, never raw dict access.
- NEVER hardcode the Kalshi fee constant -- pull the current fee schedule (Slice 1).
- `live` execution stays LOCKED until `RiskManager.live_gate()` passes (Slice 3).
  Paper/demo only until then.
- Secrets live in `.env` (gitignored) and the key `.pem` (gitignored). Never commit them.
- Confirm NBA sports-contract availability in GA before any live order (Slice 5).
- Sport-agnostic core: NBA is the first adapter (`KXNBAGAME` games, `KXNBA` Finals).
  `KXWNBAGAME` is the WNBA equivalent and shares all code.
