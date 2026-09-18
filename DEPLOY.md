# Deploying

This repo is the **engine**. It has no web UI: the board lives in a separate Next.js app.

## Topology

```
paper_pass.sh (hourly, launchd)
  └─ SQLite (primary)  ──mirror──>  Supabase kalshi_* tables
                                            │  service role only, RLS on, no policies
              Next.js on Vercel ────────────┘  Server Components read; nothing reads from a browser
```

The board is `~/projects/kalshi-edge-board`, deployed at
<https://kalshi-edge-board-lake.vercel.app> (use the suffixed host; the bare name is not
the alias). It is password gated and fails closed.

## Running the engine

The scheduled pass is `~/ClaudeCode/kalshi-edge-ops/paper_pass.sh`, invoked hourly by
`~/Library/LaunchAgents/com.intellovate.kalshi-paper-pass.plist`. It injects credentials
from the macOS Keychain, so there are no keys on disk:

```
secret-sync run ODDS_API_KEY,BALLDONTLIE_API_KEY,KALSHI_KEY_ID,SUPABASE_SERVICE_ROLE_KEY \
  -- uv run python -m kalshi_edge.paper_pass
```

Run it by hand from the repo root; `db_path` and the odds cache are cwd-relative, so the
`cd` is load-bearing.

`.env` holds non-secret config only (`KALSHI_ENV`, `KALSHI_PRIVATE_KEY_PATH`, `DATA_TIER`,
`SUPABASE_URL`). Every secret is in the Keychain and registered in
`~/.claude/secrets-manifest.tsv`; `secret-sync doctor` reports what is missing.

## Notes

- Market **data** reads from Kalshi need no credential. Only order placement does.
- The mirror fails open: if Supabase is unreachable the pass still trades and still writes
  SQLite. The board goes stale, which is the correct trade-off.
- The board never calls the Odds API. Its free tier is 500 requests/month, so a public page
  hitting it per load would exhaust the quota in a day.

## History

Until 2026-09-18 the board was a Streamlit app on Streamlit Community Cloud at
`intellovatebets.streamlit.app`. It was retired because it structurally could not show the
ledger: `data/` is gitignored so the SQLite file never deployed, and Streamlit Cloud's
filesystem is ephemeral, so every hosted page rendered against an empty database.
