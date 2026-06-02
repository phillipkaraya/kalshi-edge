"""Streamlit Community Cloud entrypoint.

The cloud installs requirements.txt (not the uv/src package), so we put ``src/`` on
the import path before loading the Edge Board. In the Streamlit Cloud app settings,
set **Main file path** = ``streamlit_app.py`` and add ``ODDS_API_KEY`` /
``BALLDONTLIE_API_KEY`` under **Secrets**. Do NOT add the Kalshi key in the cloud --
the hosted board is read-only and never places orders.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import kalshi_edge.ui.app  # noqa: E402, F401  -- importing runs the Streamlit board
