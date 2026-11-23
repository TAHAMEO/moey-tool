"""Uploads sent in pieces (/uploads): what the web app uses, since the Windows app's web server
keeps only the first 10 MB of a forwarded request."""

from __future__ import annotations

import hashlib
import subprocess
import uuid

from clipforge.config import get_settings
from clipforge.services import chunked_uploads as cu

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _video(path, seconds=3) -> bytes:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x240:r=25:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:d={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(path)], check=True, timeout=60)
    return path.read_bytes()


def _send(client, upload_id: str, data: bytes, piece: int) -> None:
    for offset in range(0, len(data), piece):
        res = client.put(f"/api/uploads/{upload_id}?offset={offset}", content=data[offset:offset + piece],
                         headers={"content-type": "application/octet-stream"})
        assert res.status_code == 200, res.text
        assert res.json()["received"] == min(len(data), offset + piece)


def test_video_in_pieces_becomes_an_upload(owner_client, tmp_path, monkeypatch):
    monkeypatch.setattr(cu, "CHUNK_BYTES", 64 * 1024)  # many pieces from a small test file
    data = _video(tmp_path / "talk.mp4")
    start = owner_client.post("/api/uploads", json={"filename": "My talk.mp4", "size": len(data), "mime": "video/mp4"})
    assert start.status_code == 200, start.text
    session = start.json()
    assert session["received"] == 0 and session["chunk_size"] == 64 * 1024
    _send(owner_client, session["upload_id"], data, 64 * 1024)
    assert owner_client.get(f"/api/uploads/{session['upload_id']}").json()["complete"] is True

    done = owner_client.post("/api/chat/upload/complete", json={"upload_id": session["upload_id"]})
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["filename"] == "My talk.mp4" and body["size"] == len(data) and body["duplicate"] is False

    from clipforge.db.base import session_scope
    from clipforge.db.models import MediaAsset
    from clipforge.storage import get_storage

    with session_scope() as db:
        asset = db.get(MediaAsset, uuid.UUID(body["asset_id"]))
        assert asset.kind == "upload" and asset.sha256 == hashlib.sha256(data).hexdigest()
        assert get_storage().local_path(asset.storage_key).read_bytes() == data
    # The upload is used up and its work folder gone.
    assert owner_client.post("/api/chat/upload/complete", json={"upload_id": session["upload_id"]}).status_code == 404
    assert not any(p.name.startswith(session["upload_id"]) for p in (get_settings().work_dir / "chunked").iterdir())

    # The same file again is recognised.
    again = owner_client.post("/api/uploads", json={"filename": "copy.mp4", "size": len(data)}).json()
    _send(owner_client, again["upload_id"], data, 64 * 1024)
    dup = owner_client.post("/api/chat/upload/complete", json={"upload_id": again["upload_id"]}).json()
    assert dup["duplicate"] is True and dup["asset_id"] == body["asset_id"]


def test_resume_after_a_lost_reply_and_gaps_refused(owner_client, tmp_path, monkeypatch):
    monkeypatch.setattr(cu, "CHUNK_BYTES", 32 * 1024)
    data = _video(tmp_path / "clip.mp4")
    upload_id = owner_client.post("/api/uploads", json={"filename": "clip.mp4", "size": len(data)}).json()["upload_id"]
    piece = 32 * 1024
    assert owner_client.put(f"/api/uploads/{upload_id}?offset=0", content=data[:piece]).status_code == 200
    # The same piece again (its reply was lost): accepted, nothing doubled.
    again = owner_client.put(f"/api/uploads/{upload_id}?offset=0", content=data[:piece])
    assert again.status_code == 200 and again.json()["received"] == piece
    # A gap: refused, with how much has arrived.
    gap = owner_client.put(f"/api/uploads/{upload_id}?offset={3 * piece}", content=data[3 * piece:4 * piece])
    assert gap.status_code == 409 and gap.json()["error"]["received"] == piece
    # Too big a piece.
    big = owner_client.put(f"/api/uploads/{upload_id}?offset={piece}", content=data[piece:piece * 3])
    assert big.status_code == 413
    # Not finished yet.
    early = owner_client.post("/api/chat/upload/complete", json={"upload_id": upload_id})
    assert early.status_code == 409 and early.json()["error"]["code"] == "incomplete"
    for offset in range(piece, len(data), piece):
        assert owner_client.put(f"/api/uploads/{upload_id}?offset={offset}", content=data[offset:offset + piece]).status_code == 200
    assert owner_client.post("/api/chat/upload/complete", json={"upload_id": upload_id}).status_code == 200


def test_checks_before_and_after(owner_client):
    wrong = owner_client.post("/api/uploads", json={"filename": "setup.exe", "size": 100})
    assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "type"
    huge = owner_client.post("/api/uploads", json={"filename": "film.mp4", "size": (get_settings().max_upload_mb + 1) << 20})
    assert huge.status_code == 413
    text = b"this is not a video at all" * 10
    upload_id = owner_client.post("/api/uploads", json={"filename": "fake.mp4", "size": len(text)}).json()["upload_id"]
    owner_client.put(f"/api/uploads/{upload_id}?offset=0", content=text)
    res = owner_client.post("/api/chat/upload/complete", json={"upload_id": upload_id})
    assert res.status_code == 400 and res.json()["error"]["code"] == "not_media"
    assert owner_client.get("/api/uploads/not-an-id").status_code == 404
    # Cancelling removes it.
    upload_id = owner_client.post("/api/uploads", json={"filename": "a.mp4", "size": 10}).json()["upload_id"]
    assert owner_client.delete(f"/api/uploads/{upload_id}").json() == {"ok": True}
    assert owner_client.get(f"/api/uploads/{upload_id}").status_code == 404


def test_library_item_from_pieces(owner_client):
    upload_id = owner_client.post("/api/uploads", json={"filename": "logo.png", "size": len(PNG),
                                                        "purpose": "library"}).json()["upload_id"]
    owner_client.put(f"/api/uploads/{upload_id}?offset=0", content=PNG)
    no_rights = owner_client.post("/api/library/from-upload", json={"upload_id": upload_id, "kind": "logo"})
    assert no_rights.status_code == 400 and no_rights.json()["error"]["code"] == "rights"
    ok = owner_client.post("/api/library/from-upload", json={"upload_id": upload_id, "kind": "logo",
                                                             "rights_confirmed": True, "title": "Brand"})
    assert ok.status_code == 200, ok.text
    items = owner_client.get("/api/library").json()
    assert items[0]["title"] == "Brand" and items[0]["kind"] == "logo"


def test_one_request_upload_still_works(owner_client, tmp_path):
    data = _video(tmp_path / "one.mp4")
    res = owner_client.post("/api/chat/upload", files={"file": ("one.mp4", data, "video/mp4")})
    assert res.status_code == 200, res.text
    assert res.json()["size"] == len(data)
    assert not list(get_settings().work_dir.glob("upload-*")), "the temporary folder is removed"
