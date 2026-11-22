"""The Windows app's process manager (clipforge/desktop.py): settings, environment, and a full
start/stop with the bundled PostgreSQL when pgserver is installed (it is on Windows)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from clipforge import desktop


def test_config_is_created_once_and_kept(tmp_path):
    layout = desktop.Layout(tmp_path)
    first = desktop.ensure_config(layout)
    assert len(first["CLIPFORGE_MASTER_KEY"]) > 30 and len(first["CLIPFORGE_DB_PASSWORD"]) > 20
    assert first["CLIPFORGE_WEB_PORT"] == "3000" and first["CLIPFORGE_API_PORT"] == "38100"
    (tmp_path / "config.env").write_text((tmp_path / "config.env").read_text() + "CLIPFORGE_OPENAI_API_KEY=sk-test\n")
    second = desktop.ensure_config(layout)
    assert second["CLIPFORGE_MASTER_KEY"] == first["CLIPFORGE_MASTER_KEY"]  # never replaced: it decrypts your accounts
    assert second["CLIPFORGE_OPENAI_API_KEY"] == "sk-test"
    if os.name != "nt":
        assert (tmp_path / "config.env").stat().st_mode & 0o077 == 0  # private to the user


def test_environment_is_local_and_single_person(tmp_path):
    layout = desktop.Layout(tmp_path)
    config = desktop.ensure_config(layout)
    env = desktop.environment(layout, config)
    assert env["CLIPFORGE_LOCAL_MODE"] == "true"
    assert env["CLIPFORGE_DATABASE_URL"].endswith("@127.0.0.1:38101/clipforge")
    assert config["CLIPFORGE_DB_PASSWORD"] in env["CLIPFORGE_DATABASE_URL"]
    assert "CLIPFORGE_DB_PASSWORD" not in env
    assert env["CLIPFORGE_PUBLIC_BASE_URL"] == "http://localhost:3000"
    assert env["CLIPFORGE_DATA_DIR"] == str(tmp_path / "data")
    assert env["CLIPFORGE_INBOX_DIR"].endswith("ClipForge")
    assert env["CLIPFORGE_COOKIE_SECURE"] == "false"
    assert env["CLIPFORGE_REDIS_URL"] == "none"


def test_no_redis_means_no_connection_attempts(monkeypatch):
    from clipforge.config import get_settings
    from clipforge.services import redis_client

    monkeypatch.setenv("CLIPFORGE_REDIS_URL", "none")
    get_settings.cache_clear()
    try:
        assert redis_client.get_redis() is None
    finally:
        monkeypatch.delenv("CLIPFORGE_REDIS_URL")
        get_settings.cache_clear()


def test_time_zone_data_for_postgresql(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    assert desktop.ensure_timezones(share) is True
    zone = share / "timezone"
    assert (zone / "UTC").read_bytes()[:4] == b"TZif" and (zone / "Europe" / "London").exists()
    assert not list(zone.rglob("__init__.py")) and not list(zone.rglob("*.tab"))
    assert desktop.ensure_timezones(share) is False  # already there


@pytest.mark.skipif(importlib.util.find_spec("pgserver") is None, reason="pgserver (bundled PostgreSQL) not installed")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="PostgreSQL refuses to run as root")
def test_full_start_and_stop(tmp_path):
    root = tmp_path / "ClipForge"
    (root / "config.env").parent.mkdir(parents=True)
    (root / "config.env").write_text("CLIPFORGE_WEB_PORT=3311\nCLIPFORGE_API_PORT=38111\nCLIPFORGE_DB_PORT=38112\n")
    cli = [sys.executable, "-m", "clipforge.cli", "desktop"]
    assert subprocess.run([*cli, "init", "--root", str(root)], timeout=300, check=False).returncode == 0
    run = subprocess.Popen([*cli, "run", "--root", str(root)])
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:38111/api/auth/status", timeout=2) as response:
                    assert b'"local":true' in response.read()
                    break
            except OSError:
                time.sleep(1)
        else:
            pytest.fail("the API did not answer")
    finally:
        assert subprocess.run([*cli, "stop", "--root", str(root)], timeout=120, check=False).returncode == 0
        run.wait(timeout=60)
    assert (root / "run" / "status.txt").read_text() == "stopped"
    assert Path(root / "data" / "postgres" / "PG_VERSION").exists()
