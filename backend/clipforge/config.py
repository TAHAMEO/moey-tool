"""Runtime configuration.

All settings come from environment variables prefixed with ``CLIPFORGE_`` (or a
``.env`` file). Nothing secret has a usable default in production: the master
key that encrypts API keys and OAuth tokens must be supplied explicitly.
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLIPFORGE_",
        env_file=(str(REPO_DIR / ".env"), ".env"),
        extra="ignore",
        # An empty value (e.g. ``CLIPFORGE_YOUTUBE_API_KEY=`` in .env) means "not set".
        env_ignore_empty=True,
    )

    env: Literal["development", "production", "test"] = "development"

    # --- Infrastructure -------------------------------------------------------
    database_url: str = "postgresql+psycopg://clipforge:clipforge_dev_pw@127.0.0.1:5432/clipforge"
    redis_url: str = "redis://127.0.0.1:6379/0"
    data_dir: Path = REPO_DIR / "var"

    # --- Security ---------------------------------------------------------------
    #: base64 (url-safe or standard) encoding of 32 random bytes. Encrypts every
    #: stored API key and OAuth token. Media workers run WITHOUT it on purpose.
    master_key: SecretStr | None = None
    #: Previous master keys (comma separated) still accepted for decryption so a
    #: key can be rotated without losing secrets.
    master_key_previous: SecretStr | None = None
    session_ttl_hours: int = 24 * 14
    #: Single-person app on this computer (the Windows app): no sign-in, every request is the
    #: owner. Only safe while CLIPFORGE listens on 127.0.0.1 alone, which the Windows app does.
    local_mode: bool = False
    cookie_secure: bool | None = None  # defaults to True in production
    #: Public URL of the web app (used for OAuth redirect URIs and links).
    public_base_url: str = "http://localhost:3000"
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    login_max_failures: int = 8
    #: Reverse proxies in front of the API that append the client address to
    #: X-Forwarded-For (1 = the bundled Caddy). 0 ignores the header entirely.
    #: Only the right-most entries are trusted: anything further left was sent
    #: by the client and can be forged.
    trusted_proxy_hops: int = Field(1, ge=0, le=5)
    login_lockout_minutes: int = 15

    # --- Workers ----------------------------------------------------------------
    #: Queues this process serves when started as a worker.
    worker_queues: str = "default,media"
    worker_concurrency: int = 1
    job_lease_seconds: int = 180
    #: Hosts allowed for direct media URLs even though they resolve to private
    #: addresses (development/test only; ignored in production).
    ssrf_private_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Media --------------------------------------------------------------------
    max_upload_mb: int = 4096
    max_download_mb: int = 8192
    max_source_duration_hours: float = 6.0
    #: Root of the folders CLIPFORGE may watch for new videos (default: <data_dir>/inbox).
    inbox_dir: Path | None = None
    #: How that folder is called on the owner's computer (shown in the app), e.g. C:\Users\me\Videos\ClipForge.
    inbox_display: str | None = None
    #: How often watched folders are checked for new videos.
    folder_scan_seconds: int = 60
    ffmpeg_threads: int = 0
    vosk_model_path: Path | None = None
    whisperx_python: Path | None = None  # interpreter of the isolated WhisperX venv
    faster_whisper_model: str = "small"
    #: With no speech model installed, download the faster-whisper model the first time a video
    #: needs one (about 480 MB, from its publisher on Hugging Face) instead of failing.
    auto_download_speech_model: bool = True
    #: The local Ollama server (local AI for Brain Chat; see providers/local_ai.py).
    ollama_url: str = "http://127.0.0.1:11434"
    #: Connect a running Ollama by itself when Brain Chat needs a model and none is connected.
    local_ai_autoconnect: bool = True
    live_srt_port: int = 9000
    live_srt_port_count: int = 4
    live_bind_host: str = "0.0.0.0"
    live_public_host: str = "localhost"
    live_segment_seconds: int = 10
    live_reconnect_grace_s: float = 120.0
    live_max_session_hours: float = 12.0

    # --- Storage --------------------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint_url: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    #: Public base URL from which stored media can be fetched by Instagram when
    #: URL-based (rather than resumable) Reels upload is used.
    media_public_base_url: str | None = None

    # --- Platform apps (owner supplies these; see docs/API_SETUP.md) -----------------
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    youtube_api_key: SecretStr | None = None
    meta_app_id: str | None = None
    meta_app_secret: SecretStr | None = None
    meta_client_token: SecretStr | None = None
    #: web: redirect registered in Google Cloud · desktop: installed-app client (loopback redirect)
    google_client_kind: str = "web"
    #: HTTPS page that forwards Instagram's sign-in result to CLIPFORGE on this computer (optional)
    instagram_relay_url: str | None = None
    #: OAuth apps built into this copy (see docs/ONE_CLICK_CONNECT.md); also looked for in /config
    oauth_apps_file: Path | None = None
    instagram_graph_base: str = "https://graph.instagram.com"
    instagram_api_version: str = "v24.0"
    google_api_base: str = "https://www.googleapis.com"
    google_oauth_base: str = "https://oauth2.googleapis.com"
    youtube_oembed_url: str = "https://www.youtube.com/oembed"
    youtube_analytics_base: str = "https://youtubeanalytics.googleapis.com/v2"

    # --- Misc -----------------------------------------------------------------------
    log_level: str = "INFO"

    @field_validator("data_dir", mode="after")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        return value.expanduser().resolve()

    @field_validator("allowed_origins", "ssrf_private_allowlist", mode="before")
    @classmethod
    def _split_csv(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                import json

                return json.loads(value)
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    # ---------------------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.is_production

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def library_dir(self) -> Path:
        """Owner-managed media library (licensed music, B-roll, SFX, masters)."""
        return self.data_dir / "library"

    @property
    def inbox(self) -> Path:
        """Watched folders live here (Docker: the host folder mounted at ``/inbox``)."""
        return self.inbox_dir or self.data_dir / "inbox"

    @property
    def sync_database_url(self) -> str:
        return self.database_url

    @property
    def psycopg_dsn(self) -> str:
        """DSN usable by plain psycopg (LangGraph checkpointer, Mem0 pgvector)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://")

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.models_dir, self.media_dir, self.work_dir,
                     self.backups_dir, self.library_dir):
            path.mkdir(parents=True, exist_ok=True)
        try:
            self.inbox.mkdir(parents=True, exist_ok=True)
        except OSError:  # a read-only or not-yet-mounted inbox is reported by the folder views instead
            pass

    def resolved_master_keys(self) -> list[bytes]:
        """Return [current, *previous] decoded keys (may be empty in media workers)."""
        keys: list[bytes] = []
        if self.master_key is not None:
            keys.append(_decode_key(self.master_key.get_secret_value()))
        elif not self.is_production and os.environ.get("CLIPFORGE_DISABLE_DEV_KEY") != "1":
            keys.append(_dev_master_key(self.data_dir))
        if self.master_key_previous is not None:
            for item in self.master_key_previous.get_secret_value().split(","):
                if item.strip():
                    keys.append(_decode_key(item.strip()))
        return keys


def _decode_key(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):  # pragma: no cover - fall back to standard alphabet
        raw = base64.b64decode(padded)
    if len(raw) != 32:
        raise ValueError("CLIPFORGE_MASTER_KEY must decode to exactly 32 bytes")
    return raw


def _dev_master_key(data_dir: Path) -> bytes:
    """Development convenience: a random key persisted with 0600 permissions."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "dev-master.key"
    if not path.exists():
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(base64.urlsafe_b64encode(key))
    return _decode_key(path.read_text(encoding="utf-8").strip())


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
