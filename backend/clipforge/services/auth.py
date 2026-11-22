"""Accounts and sessions.

Opaque random session tokens (only their SHA-256 is stored), HttpOnly cookies,
per-session CSRF tokens, login throttling with lockout, Argon2id passwords.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import utcnow
from ..db.models import User, UserSession
from ..security import passwords
from ..security.audit import audit
from . import redis_client

ROLES = ("viewer", "editor", "admin", "owner")


class AuthError(Exception):
    def __init__(self, message: str, *, code: str = "auth_failed"):
        super().__init__(message)
        self.code = code


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def role_at_least(role: str, minimum: str) -> bool:
    return ROLES.index(role) >= ROLES.index(minimum)


def users_exist(db: Session) -> bool:
    return bool(db.execute(select(func.count()).select_from(User)).scalar_one())


def create_owner(db: Session, *, email: str, password: str, display_name: str = "", ip: str | None = None) -> User:
    # Serialise first-run setup so two concurrent requests cannot both succeed.
    db.execute(text("SELECT pg_advisory_xact_lock(424242)"))
    if users_exist(db):
        raise AuthError("Setup has already been completed.", code="setup_done")
    passwords.validate_strength(password)
    user = User(email=email.strip().lower(), password_hash=passwords.hash_password(password),
                display_name=display_name.strip() or email.split("@")[0], role="owner")
    db.add(user)
    db.flush()
    audit(db, "auth.setup_owner", user_id=user.id, target_type="user", target_id=user.id, ip=ip)
    return user


LOCAL_OWNER_EMAIL = "owner@clipforge.local"


def local_owner(db: Session) -> User:
    """The one person using a single-person installation (``local_mode``); created on first use."""
    db.execute(text("SELECT pg_advisory_xact_lock(424242)"))
    user = db.execute(select(User).where(User.role == "owner", User.is_active.is_(True))
                      .order_by(User.created_at).limit(1)).scalar_one_or_none()
    if user is None:
        import secrets

        # Nobody signs in with it: the password is random and never shown.
        user = User(email=LOCAL_OWNER_EMAIL, password_hash=passwords.hash_password(secrets.token_urlsafe(48)),
                    display_name="You", role="owner")
        db.add(user)
        db.flush()
        audit(db, "auth.local_owner", user_id=user.id, target_type="user", target_id=user.id)
    return user


def create_user(db: Session, *, email: str, password: str, role: str, created_by: uuid.UUID, ip: str | None = None) -> User:
    if role not in ROLES or role == "owner":
        raise AuthError("Invalid role.", code="invalid_role")
    passwords.validate_strength(password)
    if db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none():
        raise AuthError("A user with this e-mail already exists.", code="exists")
    user = User(email=email.strip().lower(), password_hash=passwords.hash_password(password),
                display_name=email.split("@")[0], role=role)
    db.add(user)
    db.flush()
    audit(db, "auth.create_user", user_id=created_by, target_type="user", target_id=user.id,
          details={"role": role}, ip=ip)
    return user


def authenticate(db: Session, *, email: str, password: str, ip: str | None) -> User:
    settings = get_settings()
    email = email.strip().lower()
    lock_key = f"cf:login-fail:{email}"
    ip_key = f"cf:login-fail-ip:{ip or 'unknown'}"
    if (redis_client.get_count(lock_key) >= settings.login_max_failures
            or redis_client.get_count(ip_key) >= settings.login_max_failures * 5):
        audit(db, "auth.login_locked", details={"email": email}, ip=ip)
        raise AuthError("Too many failed attempts. Try again later.", code="locked")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    valid = passwords.verify_password(user.password_hash if user else None, password)
    if not valid or user is None or not user.is_active:
        window = settings.login_lockout_minutes * 60
        redis_client.incr_window(lock_key, window)
        redis_client.incr_window(ip_key, window)
        audit(db, "auth.login_failed", details={"email": email}, ip=ip)
        raise AuthError("Invalid e-mail or password.")
    redis_client.reset(lock_key)
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    user.last_login_at = utcnow()
    audit(db, "auth.login", user_id=user.id, ip=ip)
    return user


def create_session(db: Session, user: User, *, ip: str | None, user_agent: str | None) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=_hash(token),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
        ip=ip,
        user_agent=(user_agent or "")[:400],
    )
    db.add(session)
    db.flush()
    return token, session


def resolve_session(db: Session, token: str | None) -> UserSession | None:
    if not token or len(token) > 200:
        return None
    session = db.execute(select(UserSession).where(UserSession.token_hash == _hash(token))).scalar_one_or_none()
    if session is None:
        return None
    now = utcnow()
    if session.expires_at <= now or not session.user.is_active:
        db.delete(session)
        return None
    if (now - session.last_seen_at).total_seconds() > 300:
        session.last_seen_at = now
    return session


def revoke_session(db: Session, token: str | None) -> None:
    if not token:
        return
    session = db.execute(select(UserSession).where(UserSession.token_hash == _hash(token))).scalar_one_or_none()
    if session is not None:
        db.delete(session)


def change_password(db: Session, user: User, *, current: str, new: str, keep_session_id: uuid.UUID, ip: str | None) -> None:
    if not passwords.verify_password(user.password_hash, current):
        raise AuthError("Current password is incorrect.")
    passwords.validate_strength(new)
    user.password_hash = passwords.hash_password(new)
    for other in db.execute(select(UserSession).where(UserSession.user_id == user.id)).scalars():
        if other.id != keep_session_id:
            db.delete(other)
    audit(db, "auth.password_changed", user_id=user.id, ip=ip)
