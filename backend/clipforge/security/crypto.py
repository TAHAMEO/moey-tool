"""Encryption of stored credentials (API keys, OAuth tokens).

AES-256-GCM with a random 96-bit nonce per secret. The associated data binds a
ciphertext to its database row, so a ciphertext copied into another row fails
authentication instead of decrypting. Keys are identified by a short
fingerprint so the master key can be rotated (old keys stay decrypt-only).

Processes started without a master key (the isolated media workers) can store
nothing and read nothing: :class:`SecretsUnavailable` is raised.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Secret

NONCE_BYTES = 12


class SecretsUnavailable(RuntimeError):
    """This process has no master key (by design for media workers)."""


class SecretDecryptionError(RuntimeError):
    pass


def key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"clipforge-key-id:" + key).hexdigest()[:16]


class SecretBox:
    def __init__(self, keys: list[bytes]):
        self._keys = {key_fingerprint(k): k for k in keys}
        self._current = key_fingerprint(keys[0]) if keys else None

    @property
    def available(self) -> bool:
        return self._current is not None

    def encrypt(self, plaintext: bytes, aad: bytes) -> tuple[bytes, str]:
        if self._current is None:
            raise SecretsUnavailable("No master key configured in this process")
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self._current]).encrypt(nonce, plaintext, aad)
        return nonce + ciphertext, self._current

    def decrypt(self, blob: bytes, key_id: str, aad: bytes) -> bytes:
        if not self._keys:
            raise SecretsUnavailable("No master key configured in this process")
        key = self._keys.get(key_id)
        if key is None:
            raise SecretDecryptionError("Secret was encrypted with an unknown master key")
        try:
            return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
        except Exception as exc:  # cryptography raises InvalidTag
            raise SecretDecryptionError("Secret failed authentication") from exc

    def needs_rotation(self, key_id: str) -> bool:
        return self._current is not None and key_id != self._current


_box: SecretBox | None = None


def get_box() -> SecretBox:
    global _box
    if _box is None:
        _box = SecretBox(get_settings().resolved_master_keys())
    return _box


def reset_box() -> None:
    global _box
    _box = None


def mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:3]}…{value[-4:]}"


def _aad(secret_id: uuid.UUID, purpose: str) -> bytes:
    return f"clipforge-secret:{secret_id}:{purpose}".encode()


def store_secret(db: Session, purpose: str, value: str | dict[str, Any], *, hint: str | None = None) -> Secret:
    """Encrypt and persist a secret. Returns the (flushed) row."""
    payload = json.dumps(value) if isinstance(value, dict) else value
    secret_id = uuid.uuid4()
    blob, key_id = get_box().encrypt(payload.encode(), _aad(secret_id, purpose))
    row = Secret(
        id=secret_id,
        purpose=purpose,
        ciphertext=blob,
        key_id=key_id,
        hint=hint if hint is not None else (mask(payload) if isinstance(value, str) else "stored"),
    )
    db.add(row)
    db.flush()
    return row


def update_secret(db: Session, secret: Secret, value: str | dict[str, Any], *, hint: str | None = None) -> Secret:
    payload = json.dumps(value) if isinstance(value, dict) else value
    blob, key_id = get_box().encrypt(payload.encode(), _aad(secret.id, secret.purpose))
    secret.ciphertext = blob
    secret.key_id = key_id
    if hint is not None:
        secret.hint = hint
    elif isinstance(value, str):
        secret.hint = mask(value)
    db.flush()
    return secret


def read_secret(db: Session, secret_id: uuid.UUID) -> str:
    row = db.get(Secret, secret_id)
    if row is None:
        raise SecretDecryptionError("Secret not found")
    plaintext = get_box().decrypt(row.ciphertext, row.key_id, _aad(row.id, row.purpose))
    if get_box().needs_rotation(row.key_id):
        update_secret(db, row, plaintext.decode(), hint=row.hint)
    return plaintext.decode()


def read_secret_json(db: Session, secret_id: uuid.UUID) -> dict[str, Any]:
    return json.loads(read_secret(db, secret_id))


def delete_secret(db: Session, secret_id: uuid.UUID | None) -> None:
    if secret_id is None:
        return
    row = db.get(Secret, secret_id)
    if row is not None:
        db.delete(row)
        db.flush()
