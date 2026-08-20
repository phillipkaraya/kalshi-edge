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
        action: str = "buy",  # "buy" | "sell"
        side: str,  # "yes" | "no"
        count: int,
        type_: str = "limit",  # "limit" | "market"
        price_cents: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Place an order (authenticated).

        The body shape comes from :func:`build_order_body`, which is pure so the
        payload can be asserted in tests without touching the network. See the
        WARNING there: neither schema has been exercised against a real account.
        """
        body = build_order_body(
            ticker=ticker,
            action=action,
            side=side,
            count=count,
            type_=type_,
            price_cents=price_cents,
            client_order_id=client_order_id,
            schema=self.settings.kalshi_order_schema,
            time_in_force=self.settings.kalshi_time_in_force,
        )
        return self._request("POST", "portfolio/orders", json=body, auth=True)

    def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch one order's current state (authenticated), for fill reconciliation."""
        return self._request("GET", f"portfolio/orders/{order_id}", auth=True)

    # --- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KalshiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --- order body ---------------------------------------------------------------
# Kalshi's classic order body (action + yes/no side + integer cent prices) was slated
# for deprecation no earlier than 2026-05-06. The current V2 shape quotes a SINGLE YES
# book: `side` is bid/ask, prices and counts are fixed-point decimal STRINGS in dollars,
# and time_in_force / self_trade_prevention_type are required.
#
# WARNING: neither shape has been sent to a real Kalshi account from this codebase.
# HARDENING.md #7 stays open until a credentialed demo dry-run confirms one.
_STP_DEFAULT = "taker_at_cross"


def build_order_body(
    *,
    ticker: str,
    side: str,  # "yes" | "no" -- this project's own vocabulary
    count: int,
    action: str = "buy",
    type_: str = "limit",
    price_cents: int | None = None,
    client_order_id: str | None = None,
    schema: str = "v2",
    time_in_force: str = "immediate_or_cancel",
) -> dict[str, Any]:
    """Build the create-order payload for the requested schema.

    The V2 mapping is the subtle part. There is one book, quoted in YES terms, so
    buying NO at price ``p`` is expressed as SELLING YES at ``1 - p``:

        buy YES @ 0.42  ->  {"side": "bid", "price": "0.4200"}
        buy NO  @ 0.42  ->  {"side": "ask", "price": "0.5800"}

    Getting that inversion wrong would place a real order on the opposite side of the
    market at the wrong price, which is why it lives in a pure function with tests.
    """
    if side not in ("yes", "no"):
        raise ValueError(f"side must be 'yes' or 'no', got {side!r}")
    if count <= 0:
        raise ValueError(f"count must be positive, got {count}")

    if schema == "legacy":
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
        return body

    if schema != "v2":
        raise ValueError(f"unknown order schema {schema!r}")
    if type_ == "limit" and price_cents is None:
        raise ValueError("limit orders need a price")

    body = {
        "ticker": ticker,
        # buy YES = bid on the YES book; buy NO = ask (sell YES) on the same book.
        "side": "bid" if side == "yes" else "ask",
        "count": f"{count}.00",
        "time_in_force": time_in_force,
        "self_trade_prevention_type": _STP_DEFAULT,
    }
    if price_cents is not None:
        yes_cents = price_cents if side == "yes" else 100 - price_cents
        body["price"] = f"{yes_cents / 100:.4f}"
    if client_order_id:
        body["client_order_id"] = client_order_id
    return body


def parse_fill(payload: dict[str, Any]) -> tuple[int, str]:
    """Read (filled_contracts, normalized_status) out of an order/create response.

    Kalshi reports counts as fixed-point strings and uses several names across
    endpoints (``fill_count`` on create, ``taker_fill_count``/``remaining_count`` on
    fetch), with status one of resting/pending/executed/canceled. Anything we cannot
    read is reported as zero filled and still-pending, so an unparsed response can
    never be mistaken for a confirmed fill.
    """
    order = payload.get("order", payload) if isinstance(payload, dict) else {}
    if not isinstance(order, dict):
        return 0, "pending"

    def _int(*names: str) -> int | None:
        for n in names:
            v = order.get(n)
            if v is None:
                continue
            try:
                return int(float(v))
            except (TypeError, ValueError):
                continue
        return None

    filled = _int("fill_count", "taker_fill_count", "filled_count") or 0
    remaining = _int("remaining_count")
    raw = str(order.get("status", "")).lower()
    if raw in ("executed", "filled"):
        status = "filled"
    elif raw == "canceled":
        status = "canceled"
    elif raw in ("resting", "pending", ""):
        # No status we recognise: fall back to the counts. Only a zero remainder with
        # something actually filled counts as complete.
        status = "filled" if (remaining == 0 and filled > 0) else "pending"
    else:
        status = "pending"
    return max(0, filled), status
