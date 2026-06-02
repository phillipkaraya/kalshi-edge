"""RSA-PSS request signing produces a signature Kalshi's public key would verify."""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_edge.config import Settings
from kalshi_edge.kalshi.client import KalshiClient


def test_auth_headers_signature_verifies(tmp_path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)

    settings = Settings(kalshi_key_id="test-key-id", kalshi_private_key_path=key_path)
    client = KalshiClient(settings)
    assert client.authenticated

    path = "/trade-api/v2/portfolio/balance"
    headers = client._auth_headers("GET", path)
    assert headers["KALSHI-ACCESS-KEY"] == "test-key-id"
    assert set(headers) == {
        "KALSHI-ACCESS-KEY",
        "KALSHI-ACCESS-TIMESTAMP",
        "KALSHI-ACCESS-SIGNATURE",
    }

    message = f"{headers['KALSHI-ACCESS-TIMESTAMP']}GET{path}".encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    # Raises InvalidSignature if the signing is wrong; passing == verified.
    private_key.public_key().verify(
        signature,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    client.close()


def test_no_key_when_path_empty_or_missing(tmp_path) -> None:
    # A blank path coerces to Path(".") (a dir), and a configured-but-missing file must
    # also be treated as "no credentials" -> the read-only board must never crash.
    for bad in (Path(""), tmp_path / "missing.pem"):
        client = KalshiClient(Settings(kalshi_private_key_path=bad))
        assert not client.authenticated
        client.close()
