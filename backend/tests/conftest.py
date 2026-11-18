"""Test harness: real PostgreSQL (clipforge_test), real Redis, real FFmpeg.

External platforms are never mocked into "success": tests that need real
credentials are skipped with an explicit BLOCKED reason. Contract tests use
local fake servers that implement documented API behaviour and are marked
``contract``.
"""

from __future__ import annotations

import base64
import os
import secrets
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="clipforge-test-"))
os.environ.setdefault("CLIPFORGE_ENV", "test")
os.environ.setdefault(
    "CLIPFORGE_DATABASE_URL", "postgresql+psycopg://clipforge:clipforge_dev_pw@127.0.0.1:5432/clipforge_test"
)
os.environ.setdefault("CLIPFORGE_REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ.setdefault("CLIPFORGE_DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("CLIPFORGE_MASTER_KEY", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
os.environ.setdefault("CLIPFORGE_SSRF_PRIVATE_ALLOWLIST", "127.0.0.1,localhost")
os.environ.setdefault("CLIPFORGE_LOG_LEVEL", "WARNING")
os.environ["MEM0_TELEMETRY"] = "False"

# Shared real model files (downloaded once into the dev data dir) are reused.
_DEV_MODELS = Path(__file__).resolve().parents[2] / "var" / "models"
if _DEV_MODELS.exists():
    (_TMP / "data").mkdir(parents=True, exist_ok=True)
    link = _TMP / "data" / "models"
    if not link.exists():
        link.symlink_to(_DEV_MODELS)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from clipforge.config import get_settings  # noqa: E402
from clipforge.db.base import get_engine, session_scope  # noqa: E402


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = get_engine()
    with engine.begin() as conn:
        # Drop tables (not the schema): the app role may not re-create the
        # pgvector extension, which an administrator installs once.
        tables = [r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))]
        if tables:
            conn.execute(text("DROP TABLE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _database():
    get_settings.cache_clear()
    _migrate()
    yield


APP_TABLES_SQL = """
SELECT tablename FROM pg_tables WHERE schemaname='public'
AND tablename NOT IN ('alembic_version')
"""


@pytest.fixture(autouse=True)
def _clean_tables():
    engine = get_engine()
    with engine.begin() as conn:
        tables = [r[0] for r in conn.execute(text(APP_TABLES_SQL))]
        if tables:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " CASCADE"))
    from clipforge.services import redis_client

    client = redis_client.get_redis()
    if client is not None:
        client.flushdb()
    redis_client._local_counters.clear()
    yield


@pytest.fixture
def db():
    with session_scope() as session:
        yield session


@pytest.fixture
def app():
    from clipforge.api.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "Correct-Horse-42"


@pytest.fixture
def owner_client(client):
    response = client.post("/api/auth/setup", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    return client


# --------------------------------------------------------------- fake platforms
@pytest.fixture
def platforms(monkeypatch):
    """Local fake of YouTube/Drive/OAuth/Instagram APIs for CONTRACT tests."""
    from tests.fakes.platform_server import FakePlatforms

    with FakePlatforms() as fake:
        env = {
            "CLIPFORGE_GOOGLE_API_BASE": fake.base, "CLIPFORGE_GOOGLE_OAUTH_BASE": fake.base,
            "CLIPFORGE_YOUTUBE_OEMBED_URL": f"{fake.base}/oembed", "CLIPFORGE_YOUTUBE_ANALYTICS_BASE": f"{fake.base}/v2",
            "CLIPFORGE_INSTAGRAM_GRAPH_BASE": fake.base, "CLIPFORGE_GOOGLE_CLIENT_ID": "cid",
            "CLIPFORGE_GOOGLE_CLIENT_SECRET": "csecret", "CLIPFORGE_META_APP_ID": "123", "CLIPFORGE_META_APP_SECRET": "msecret",
        }
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        yield fake
    get_settings.cache_clear()


def connect(platform: str, external_id: str, meta: dict | None = None):
    """Store a connected account with (fake) OAuth tokens, encrypted like real ones."""
    from datetime import timedelta

    from clipforge.db.base import utcnow
    from clipforge.db.models import SocialAccount
    from clipforge.security.crypto import store_secret

    with session_scope() as db:
        account = SocialAccount(platform=platform, external_account_id=external_id, display_name=platform, meta=meta or {})
        db.add(account)
        db.flush()
        tokens = {"access_token": "tok-" + platform, "refresh_token": "ref",
                  "expires_at": (utcnow() + timedelta(days=30)).isoformat()}
        account.secret_id = store_secret(db, f"oauth:{platform}:{account.id}", tokens).id
        return account.id
