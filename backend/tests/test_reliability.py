"""Fixes for problems seen on Windows PCs: no speech model, memory, and files removed by clean-up."""

from __future__ import annotations

import shutil
import uuid

import pytest

from clipforge.config import get_settings
from clipforge.db.base import session_scope
from clipforge.db.models import MediaAsset, Source
from clipforge.media import models, transcription
from clipforge.storage import asset_present, get_storage


# ------------------------------------------------------------------ speech model
def test_faster_whisper_install_is_staged_and_reports_progress(monkeypatch):
    import faster_whisper

    def fake_download(size, output_dir):
        import time
        from pathlib import Path

        out = Path(output_dir)
        (out / ".cache").mkdir(parents=True, exist_ok=True)
        (out / "config.json").write_text("{}", encoding="utf-8")
        time.sleep(1.3)  # long enough for a progress report
        (out / "model.bin").write_bytes(b"\0" * 4096)
        return output_dir

    monkeypatch.setattr(faster_whisper, "download_model", fake_download)
    seen = []
    shutil.rmtree(models.faster_whisper_dir("base"), ignore_errors=True)
    assert not models.faster_whisper_installed("base")
    target = models.install_faster_whisper("base", on_progress=lambda done, total: seen.append((done, total)))
    assert target == get_settings().models_dir / "faster-whisper" / "base"
    assert (target / "model.bin").is_file() and not (target / ".cache").exists()
    assert not target.with_name("base.download").exists(), "the staging folder is moved into place"
    assert models.faster_whisper_installed("base")
    assert seen and seen[-1][0] == seen[-1][1] == 145 << 20  # finished: done == total
    shutil.rmtree(target)  # a fake model must not be picked up by other tests


def test_failed_download_leaves_no_half_model(monkeypatch):
    import faster_whisper

    def broken(size, output_dir):
        raise OSError("connection reset")

    monkeypatch.setattr(faster_whisper, "download_model", broken)
    with pytest.raises(models.ModelUnavailable, match="connection reset"):
        models.install_faster_whisper("tiny")
    assert not models.faster_whisper_installed("tiny")


def test_transcribe_job_downloads_a_speech_model_when_none_is_installed(monkeypatch):
    from clipforge.handlers import media_jobs

    calls, progress = [], []

    class Ctx:
        def progress(self, fraction=None, **kw):
            progress.append((fraction, kw.get("label"), kw.get("message")))

    engine = object()
    state = {"installed": False}

    def select(language, preference="auto"):
        if not state["installed"]:
            raise transcription.TranscriptionUnavailable("vosk: no model")
        return engine

    def install(size=None, on_progress=None):
        calls.append(size)
        on_progress(242 << 20, 484 << 20)
        state["installed"] = True

    monkeypatch.setattr(transcription, "select_engine", select)
    monkeypatch.setattr(models, "install_faster_whisper", install)
    assert media_jobs._download_speech_model(Ctx(), "en", RuntimeError("none")) is engine
    assert calls == [get_settings().faster_whisper_model]
    assert any(label == "Downloading the speech model" and message and "242 of 484 MB" in message
               for _, label, message in progress)

    # Download failing: a retryable error that says what to do.
    from clipforge.jobs.registry import PermanentError, RetryableError

    state["installed"] = False

    def offline(size=None, on_progress=None):
        raise models.ModelUnavailable("no internet")

    monkeypatch.setattr(models, "install_faster_whisper", offline)
    with pytest.raises(RetryableError, match="Retry"):
        media_jobs._download_speech_model(Ctx(), "en", RuntimeError("none"))
    monkeypatch.setattr(get_settings(), "auto_download_speech_model", False)
    with pytest.raises(PermanentError):
        media_jobs._download_speech_model(Ctx(), "en", RuntimeError("none"))


def test_release_models_frees_loaded_speech_models():
    transcription.VoskEngine._model = object()
    transcription.FasterWhisperEngine._models["x"] = object()
    transcription.release_models()
    assert transcription.VoskEngine._model is None and transcription.FasterWhisperEngine._models == {}


# ------------------------------------------------------- files removed by clean-up
def _stored(tmp_path, key: str, content: bytes = b"\x00\x00\x00\x18ftypmp42" + b"\0" * 100) -> MediaAsset:
    src = tmp_path / "f.mp4"
    src.write_bytes(content)
    get_storage().put_file(src, key)
    return MediaAsset(kind="upload", storage_key=key, mime="video/mp4", size_bytes=len(content), sha256="ab" * 32,
                      info={"filename": "f.mp4"})


def test_asset_present(tmp_path):
    asset = _stored(tmp_path, "uploads/ab/test.mp4")
    assert asset_present(asset)
    asset.info = {"deleted": True}
    assert not asset_present(asset)
    asset.info = {}
    get_storage().delete(asset.storage_key)
    assert not asset_present(asset)
    assert not asset_present(None)


def test_upload_removed_by_cleanup_is_reported_clearly(tmp_path):
    from clipforge.ingestion import acquire as acq

    with session_scope() as db:
        asset = _stored(tmp_path, "uploads/ab/gone.mp4")
        asset.info = {"deleted": True}
        db.add(asset)
        source = Source(platform="upload", source_type="file", external_id="gone", original_url="upload://gone",
                        normalized_url="upload://gone")
        db.add(source)
        db.flush()
        with pytest.raises(acq.AcquisitionError, match="removed by storage clean-up"):
            acq.acquire(db, source, {"method": "upload", "acquire": {"asset_id": str(asset.id)}})


def test_same_file_after_cleanup_is_stored_again(tmp_path):
    from clipforge.ingestion import acquire as acq

    content = b"\x00\x00\x00\x18ftypmp42" + b"\1" * 200
    with session_scope() as db:
        source = Source(platform="youtube", source_type="video", external_id="x", original_url="https://youtu.be/x",
                        normalized_url="https://youtu.be/x")
        db.add(source)
        db.flush()
        first = tmp_path / "first.mp4"
        first.write_bytes(content)
        asset = acq._store_source_media(db, source, first, sha="cd" * 32, mime="video/mp4", origin={})
        key = asset.storage_key
        # Clean-up removes the stored copy …
        get_storage().delete(key)
        asset.info = {**asset.info, "deleted": True}
        # … and the same video is downloaded again later.
        again = tmp_path / "again.mp4"
        again.write_bytes(content)
        restored = acq._store_source_media(db, source, again, sha="cd" * 32, mime="video/mp4", origin={})
        assert restored.id == asset.id and asset_present(restored)
        assert get_storage().local_path(key).read_bytes() == content


def test_render_says_why_when_the_source_video_is_gone(tmp_path):
    from clipforge.handlers import media_jobs
    from clipforge.jobs.registry import PermanentError

    with session_scope() as db:
        source = Source(platform="upload", source_type="file", external_id="y", original_url="upload://y",
                        normalized_url="upload://y")
        db.add(source)
        db.flush()
        mez = _stored(tmp_path, f"sources/{source.id}/mezzanine.mp4")
        mez.kind, mez.source_id, mez.info = "mezzanine", source.id, {"deleted": True}
        db.add(mez)
        source_id = source.id
    with pytest.raises(PermanentError, match="no longer stored"):
        media_jobs._source_paths(source_id)
    assert isinstance(source_id, uuid.UUID)


def test_rerendered_clip_is_publishable_with_its_new_video():
    """After Adjust → Re-render (or a change from Brain Chat) the packages use the new render; the
    replaced one would be refused by publishing ("not passed its quality checks")."""
    from clipforge.db.models import Clip, OutputPackage, Project, Render
    from clipforge.handlers.media_jobs import repoint_packages

    with session_scope() as db:
        source = Source(platform="upload", source_type="file", external_id="r", original_url="upload://r",
                        normalized_url="upload://r")
        db.add(source)
        db.flush()
        project = Project(title="p", source_id=source.id, request={})
        db.add(project)
        db.flush()
        clip = Clip(project_id=project.id, source_id=source.id, rank=1, start_s=0, end_s=30, scores={}, status="rendered")
        db.add(clip)
        db.flush()
        old = Render(clip_id=clip.id, aspect="9:16", status="superseded", quality_report={"passed": True})
        new = Render(clip_id=clip.id, aspect="9:16", status="done", quality_report={"passed": True})
        db.add_all([old, new])
        db.flush()
        youtube = OutputPackage(clip_id=clip.id, platform="youtube", render_id=old.id, status="ready")
        published = OutputPackage(clip_id=clip.id, platform="instagram", render_id=old.id, status="published")
        db.add_all([youtube, published])
        db.flush()
        assert repoint_packages(db, clip.id) == 1
        assert youtube.render_id == new.id
        assert published.render_id == old.id  # what went out stays as it was
