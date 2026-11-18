"""Local model installation: integrity verification and safe archive handling."""

from __future__ import annotations

import zipfile

import pytest

from clipforge.config import get_settings
from clipforge.media import models


def test_vosk_zip_slip_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "data")  # no model installed yet
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("../../outside.txt", "nope")
    with pytest.raises(models.ModelUnavailable, match="unsafe path"):
        models.install_vosk(str(evil))
    assert not (tmp_path / "data" / "outside.txt").exists() and not (tmp_path / "outside.txt").exists()
    assert not list((get_settings().models_dir / "vosk").glob(".staging-*"))


def test_vosk_tampered_archive_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "data")  # fresh, model-less data dir
    fake = tmp_path / "fake.zip"
    with zipfile.ZipFile(fake, "w") as archive:
        archive.writestr(f"{models.VOSK_MODEL}/README", "not the real model")
        archive.writestr(f"{models.VOSK_MODEL}/conf/model.conf", "--min-active=200")
    with pytest.raises(models.ModelUnavailable, match="failed verification"):
        models.install_vosk(str(fake))
    assert not (tmp_path / "data" / "models" / "vosk" / models.VOSK_MODEL).exists()


@pytest.mark.slow
def test_vosk_install_from_verified_folder(tmp_path, monkeypatch):
    source = get_settings().models_dir / "vosk" / models.VOSK_MODEL
    if not source.is_dir():
        pytest.skip("BLOCKED: reference speech model not present")
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "data")
    installed = models.install_vosk(str(source))
    assert installed == tmp_path / "data" / "models" / "vosk" / models.VOSK_MODEL
    assert models.verify_vosk_dir(installed) == []
