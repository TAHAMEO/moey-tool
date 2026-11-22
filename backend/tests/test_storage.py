"""Local storage keys stay inside the storage folder — checked as paths, so it also holds on
Windows, where folders are separated by "\\" (the earlier text check rejected every key there)."""

from __future__ import annotations

import os

import pytest

from clipforge.storage import LocalStorage, StorageError


def test_keys_resolve_inside_the_root(tmp_path):
    storage = LocalStorage(tmp_path / "media")
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"video")
    assert storage.put_file(src, "sources/abc/source.mp4") == 5
    assert storage.local_path("sources/abc/source.mp4").read_bytes() == b"video"
    assert storage.exists("sources/abc/source.mp4")


@pytest.mark.parametrize("key", ["../outside.mp4", "a/../../outside.mp4", "/etc/passwd", "a//b", ".hidden"])
def test_escaping_keys_are_refused(tmp_path, key):
    with pytest.raises(StorageError):
        LocalStorage(tmp_path / "media").path_for_write(key)


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs extra rights on Windows")
def test_a_link_pointing_outside_is_refused(tmp_path):
    root = tmp_path / "media"
    storage = LocalStorage(root)
    (tmp_path / "elsewhere").mkdir()
    (root / "sneaky").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(StorageError, match="escapes"):
        storage.path_for_write("sneaky/file.mp4")
