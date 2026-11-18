"""Password hashing with Argon2id (argon2-cffi defaults: t=3, m=64 MiB, p=4)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()
# Verifying against this when the account does not exist keeps response time
# similar for known and unknown e-mail addresses.
_DUMMY_HASH = _hasher.hash("clipforge-timing-equaliser")

MIN_LENGTH = 10


class WeakPasswordError(ValueError):
    pass


def validate_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPasswordError(f"Password must be at least {MIN_LENGTH} characters long.")
    if password.lower() == password and password.isalpha():
        raise WeakPasswordError("Use a mix of letters with numbers, symbols or capitals.")
    if len(set(password)) < 5:
        raise WeakPasswordError("Password is too repetitive.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
