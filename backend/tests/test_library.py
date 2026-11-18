"""Owner media library: rights gate, type checks, master matching copies."""

from __future__ import annotations

import subprocess

from clipforge.config import get_settings

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _video(path, seconds=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s=64x36:r=5:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", str(path)], check=True, timeout=60)
    return path.read_bytes()


def test_rights_and_type_checks(owner_client):
    no_rights = owner_client.post("/api/library", data={"kind": "logo", "rights_confirmed": "false"},
                                  files={"file": ("logo.png", PNG, "image/png")})
    assert no_rights.status_code == 400 and no_rights.json()["error"]["code"] == "rights"
    fake_png = owner_client.post("/api/library", data={"kind": "logo", "rights_confirmed": "true"},
                                 files={"file": ("logo.png", b"GIF89a....", "image/png")})
    assert fake_png.status_code == 400
    wrong_ext = owner_client.post("/api/library", data={"kind": "music", "rights_confirmed": "true"},
                                  files={"file": ("song.exe", b"MZ" * 50, "application/octet-stream")})
    assert wrong_ext.status_code == 400
    not_audio = owner_client.post("/api/library", data={"kind": "music", "rights_confirmed": "true"},
                                  files={"file": ("song.mp3", b"just text" * 20, "audio/mpeg")})
    assert not_audio.status_code == 400
    assert not list(get_settings().work_dir.glob("library-*")), "rejected files must not leave temp dirs"
    ok = owner_client.post("/api/library", data={"kind": "logo", "rights_confirmed": "true", "license_note": "own logo"},
                           files={"file": ("logo.png", PNG, "image/png")})
    assert ok.status_code == 200
    items = owner_client.get("/api/library").json()
    assert items[0]["rights_confirmed"] is True and items[0]["license_note"] == "own logo"


def test_master_copy_is_created_and_removed(owner_client, tmp_path):
    data = _video(tmp_path / "talk.mp4")
    res = owner_client.post("/api/library", data={"kind": "master", "rights_confirmed": "true"},
                            files={"file": ("My Talk.mp4", data, "video/mp4")})
    assert res.status_code == 200, res.text
    master = get_settings().library_dir / "masters" / "My Talk.mp4"
    assert master.read_bytes() == data
    assert owner_client.delete(f"/api/library/{res.json()['id']}").json() == {"ok": True}
    assert not master.exists()
