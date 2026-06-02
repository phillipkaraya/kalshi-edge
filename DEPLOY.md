# Deploying the Edge Board (free)

Streamlit **can't** run on Vercel (Vercel is for serverless / Next.js / static). The
free, purpose-built host is **Streamlit Community Cloud**.

## Steps (≈3 min)
1. Go to **https://share.streamlit.io** and sign in with the GitHub account that can
   access `IntellovateAI/kalshi-edge`.
2. **Create app → Deploy a public/private app from a repo:**
   - Repository: `IntellovateAI/kalshi-edge`
   - Branch: `main`
   - **Main file path: `streamlit_app.py`**
3. **Advanced settings → Secrets**, paste:
   ```toml
   ODDS_API_KEY = "your-odds-api-key"
   BALLDONTLIE_API_KEY = "your-balldontlie-key"
   ```
   Streamlit exposes these as environment variables, which the app's `Settings` reads.
   **Do NOT add the Kalshi key** — the hosted board is read-only and never trades.
4. **Deploy.** First build installs `requirements.txt` (~2 min), then the board is live
   at a `*.streamlit.app` URL.

## What the hosted app is (and isn't)
- A **live, shareable dashboard**: real Kalshi NBA markets + devigged fair value + edges.
- **Read-only** — no Kalshi credential in the cloud, so it can never place an order.
- The **paper-trading accumulation** (cron + SQLite ledger) stays on your Mac; the cloud
  filesystem is ephemeral, so the hosted app just recomputes live on each load.
- To keep it private: **app Settings → Sharing → invite only**, add viewer emails.

## Updating
Push to `main` → Streamlit Cloud auto-redeploys.
