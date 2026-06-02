"""Kalshi Trade API v2 client.

Market-data reads are public (no auth). Trading/portfolio endpoints require
RSA-PSS request signing: sign ``timestamp_ms + METHOD + path`` (path WITHOUT
the query string) and send three headers -- Key ID, timestamp, base64 signature.
We sign the full server path (including ``/trade-api/v2``), per Kalshi's spec.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, cast

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from ..config import Settings, get_settings
from .models import MarketsPage


class KalshiAuthError(RuntimeError):
    """Raised when an authenticated endpoint is called without credentials."""


class KalshiClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = httpx.Client(
            base_url=self.settings.kalshi_host,
            timeout=httpx.Timeout(20.0),
            headers={"Accept": "application/json"},
        )
        self._key_id = self.settings.kalshi_key_id
        self._private_key: RSAPrivateKey | None = self._load_key()

    def _load_key(self) -> RSAPrivateKey | None:
        path = self.settings.kalshi_private_key_path
        # An empty .env value coerces to Path("") == Path(".") (a dir) -> treat as unset.
        if path is None or str(path) in ("", "."):
            return None
        pem_path = Path(path).expanduser()
        # Configured but missing: treat as no creds so the read-only board never crashes.
        if not pem_path.is_file():
            return None
        return cast(
            RSAPrivateKey, serialization.load_pem_private_key(pem_path.read_bytes(), password=None)
        )

    @property
    def authenticated(self) -> bool:
        return self._private_key is not None and bool(self._key_id)

    # --- signing -------------------------------------------------------------
    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if self._private_key is None or not self._key_id:
            raise KalshiAuthError(
                "This endpoint needs Kalshi credentials. Set KALSHI_KEY_ID and "
                "KALSHI_PRIVATE_KEY_PATH in your .env."
            )
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method}{path}".encode()
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self._key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    # --- request -------------------------------------------------------------
    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = False,
    ) -> dict[str, Any]:
        path = self.settings.api_path(endpoint)
        headers = self._auth_headers(method, path) if auth else {}
        resp = self._client.request(method, path, params=params, json=json, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # --- public market data --------------------------------------------------
    def get_markets(
        self,
        *,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        status: str = "open",
        limit: int = 200,
        cursor: str | None = None,
    ) -> MarketsPage:
        params: dict[str, Any] = {"limit": limit, "status": status}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor
        return MarketsPage.model_validate(self._request("GET", "markets", params=params))

    # --- authenticated portfolio / trading ----------------------------------
    def get_balance(self) -> dict[str, Any]:
        """Account balance (authenticated)."""
        return self._request("GET", "portfolio/balance", auth=True)

    def create_order(
        self,
        *,
        ticker: str,
        action: str,  # "buy" | "sell"
        side: str,  # "yes" | "no"
        count: int,
        type_: str = "limit",  # "limit" | "market"
        price_cents: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Place an order (authenticated).

        NOTE: verify this body against Kalshi's current order schema before
        enabling live trading; it is exercised only in demo/live modes.
        """
        body: dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": type_,
        }
        if type_ == "limit" and price_cents is not None:
            body["yes_price" if side == "yes" else "no_price"] = price_cents
        if client_order_id:
            body["client_order_id"] = client_order_id
        return self._request("POST", "portfolio/orders", json=body, auth=True)

    # --- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KalshiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
