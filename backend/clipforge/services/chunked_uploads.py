"""Resumable uploads sent in pieces.

A video is sent as a series of pieces of at most ``CHUNK_BYTES`` each, then assembled. Large
single requests do not survive every hop: the Windows app's web server (Next.js) keeps only the
first 10 MB of a request body it forwards, and reverse proxies or tunnels often cap bodies too.
Small pieces pass everywhere, a lost connection costs one piece instead of the whole file, and the
browser knows exactly how much has arrived.

Each upload is a folder ``<work_dir>/chunked/<id>`` holding ``meta.json`` and ``data`` (the bytes
received so far, in order). Old unfinished uploads are removed by the cleanup job like every other
work folder.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

#: Largest piece the browser sends; well under the 10 MB a forwarded request may carry.
CHUNK_BYTES = 8 * 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class UploadError(RuntimeError):
    def __init__(self, message: str, *, code: str = "upload", status: int = 400, received: int | None = None):
        super().__init__(message)
        self.code, self.status, self.received = code, status, received


@dataclass
class Upload:
    id: str
    user_id: str
    filename: str
    size: int
    mime: str
    purpose: str
    received: int
    path: Path

    def to_dict(self) -> dict:
        return {"upload_id": self.id, "filename": self.filename, "size": self.size, "received": self.received,
                "chunk_size": CHUNK_BYTES, "complete": self.received == self.size}


def _root() -> Path:
    root = get_settings().work_dir / "chunked"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _lock(upload_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(upload_id, threading.Lock())


def _folder(upload_id: str) -> Path:
    if not _ID.match(upload_id or ""):
        raise UploadError("Upload not found.", code="not_found", status=404)
    return _root() / upload_id


def create(user_id: str, filename: str, size: int, mime: str | None, purpose: str, max_bytes: int) -> Upload:
    name = Path(filename or "upload").name[:200] or "upload"
    if size <= 0:
        raise UploadError("The file is empty.", code="empty")
    if size > max_bytes:
        raise UploadError(f"Files up to {max_bytes >> 20} MB are accepted.", code="too_large", status=413)
    upload_id = uuid.uuid4().hex
    folder = _root() / upload_id
    folder.mkdir(parents=True)
    (folder / "data").write_bytes(b"")
    meta = {"user_id": str(user_id), "filename": name, "size": int(size), "mime": mime or "application/octet-stream",
            "purpose": purpose, "created": time.time()}
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return get(upload_id, user_id)


def get(upload_id: str, user_id: str) -> Upload:
    folder = _folder(upload_id)
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        raise UploadError("Upload not found (it may have expired). Choose the file again.", code="not_found",
                          status=404) from exc
    if meta["user_id"] != str(user_id):
        raise UploadError("Upload not found.", code="not_found", status=404)
    data = folder / "data"
    return Upload(id=upload_id, user_id=meta["user_id"], filename=meta["filename"], size=int(meta["size"]),
                  mime=meta.get("mime") or "application/octet-stream", purpose=meta.get("purpose", "media"),
                  received=data.stat().st_size if data.exists() else 0, path=data)


def append(upload_id: str, user_id: str, offset: int, chunk: bytes) -> Upload:
    """Write ``chunk`` at ``offset``. A piece sent again after a lost reply (offset below what has
    arrived) is written over the same bytes; a gap is refused with what has arrived so far."""
    with _lock(upload_id):
        upload = get(upload_id, user_id)
        if len(chunk) > CHUNK_BYTES:
            raise UploadError(f"Pieces are limited to {CHUNK_BYTES >> 20} MB.", code="chunk_too_large", status=413)
        if offset < 0 or offset > upload.received:
            raise UploadError("A piece is missing; continuing from what has arrived.", code="offset", status=409,
                              received=upload.received)
        if offset + len(chunk) > upload.size:
            raise UploadError("More data than the file's size.", code="too_large", status=413)
        end = offset + len(chunk)
        with open(upload.path, "r+b") as handle:
            handle.seek(offset)
            handle.write(chunk)
            if end < upload.received:  # the browser went back: continue from the end of this piece
                handle.truncate(end)
        upload.received = upload.path.stat().st_size
        return upload


def take(upload_id: str, user_id: str) -> tuple[Upload, Path]:
    """The finished upload: its file is moved into a new temporary folder (the caller deletes it)
    and the upload is closed."""
    with _lock(upload_id):
        upload = get(upload_id, user_id)
        if upload.received != upload.size:
            raise UploadError(f"The upload is incomplete ({upload.received} of {upload.size} bytes).",
                              code="incomplete", status=409, received=upload.received)
        folder = _folder(upload_id)
        target = folder.with_name(folder.name + ".done")
        _rename(folder, target)
    with _locks_guard:
        _locks.pop(upload_id, None)
    return upload, target / "data"


def _rename(src: Path, dest: Path, attempts: int = 20) -> None:
    """Rename, waiting out the moment Windows (or a virus scanner) still holds a just-written file."""
    for attempt in range(attempts):
        try:
            src.replace(dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.25)


def cancel(upload_id: str, user_id: str) -> None:
    get(upload_id, user_id)
    shutil.rmtree(_folder(upload_id), ignore_errors=True)
    with _locks_guard:
        _locks.pop(upload_id, None)
