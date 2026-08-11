"""AES-256-GCM authenticated encryption for secrets at rest (§38)."""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.core.config import get_settings

_NONCE_LEN = 12


def encrypt_secret(plaintext: str) -> str:
    key = get_settings().encryption_key_bytes()
    nonce = os.urandom(_NONCE_LEN)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt_secret(token: str) -> str:
    key = get_settings().encryption_key_bytes()
    raw = base64.b64decode(token)
    nonce, ct = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")


def mask_secret(secret: str) -> str:
    """Display hint only — never reversible. e.g. 'sk-…4821'."""
    if len(secret) <= 8:
        return "•" * len(secret)
    return f"{secret[:3]}…{secret[-4:]}"
