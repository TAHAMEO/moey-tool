"""Object storage: local filesystem (single host) or any S3-compatible service.

Processing always happens on local files; with S3 the worker downloads into a
local cache, works, and uploads results. Keys are validated so they can never
escape the storage root.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import threading
from pathlib import Path
from typing import Protocol

from ..config import get_settings

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,400}$")


class StorageError(RuntimeError):
    pass


def check_key(key: str) -> str:
    if not _KEY_RE.match(key) or ".." in key.split("/") or "//" in key:
        raise StorageError(f"Invalid storage key: {key!r}")
    return key


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Storage(Protocol):
    def put_file(self, src: Path, key: str, *, move: bool = False) -> int: ...
    def local_path(self, key: str) -> Path: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def public_url(self, key: str, expires_s: int = 3600) -> str | None: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        path = (root / check_key(key)).resolve()
        # Compared as paths, not text: Windows separates folders with "\", not "/".
        if path == root or not path.is_relative_to(root):
            raise StorageError("Key escapes storage root")
        return path

    def put_file(self, src: Path, key: str, *, move: bool = False) -> int:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.resolve() != dest:
            (shutil.move if move else shutil.copy2)(str(src), str(dest))
        return dest.stat().st_size

    def local_path(self, key: str) -> Path:
        path = self._path(key)
        if not path.exists():
            raise StorageError(f"Missing object {key}")
        return path

    def path_for_write(self, key: str) -> Path:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    def public_url(self, key: str, expires_s: int = 3600) -> str | None:
        base = get_settings().media_public_base_url
        return f"{base.rstrip('/')}/{check_key(key)}" if base else None


class S3Storage:
    def __init__(self) -> None:
        import boto3

        settings = get_settings()
        self.bucket = settings.s3_bucket
        if not self.bucket:
            raise StorageError("CLIPFORGE_S3_BUCKET is required for the s3 storage backend")
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value() if settings.s3_access_key_id else None,
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value() if settings.s3_secret_access_key else None,
        )
        self.cache = LocalStorage(settings.data_dir / "cache" / "s3")

    def put_file(self, src: Path, key: str, *, move: bool = False) -> int:
        size = src.stat().st_size
        self.client.upload_file(str(src), self.bucket, check_key(key))
        cached = self.cache.path_for_write(key)
        if src.resolve() != cached:
            (shutil.move if move else shutil.copy2)(str(src), str(cached))
        return size

    def local_path(self, key: str) -> Path:
        cached = self.cache.path_for_write(key)
        if not cached.exists():
            tmp = cached.with_suffix(cached.suffix + ".part")
            self.client.download_file(self.bucket, check_key(key), str(tmp))
            tmp.replace(cached)
        return cached

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=check_key(key))
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=check_key(key))
        self.cache.delete(key)

    def public_url(self, key: str, expires_s: int = 3600) -> str | None:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": check_key(key)},
                                                  ExpiresIn=expires_s)


_storage = None
_lock = threading.Lock()


def get_storage():
    global _storage
    with _lock:
        if _storage is None:
            settings = get_settings()
            _storage = S3Storage() if settings.storage_backend == "s3" else LocalStorage(settings.media_dir)
        return _storage


def asset_present(asset) -> bool:
    """A media asset whose file is still stored (storage clean-up removes old source files)."""
    if asset is None or (asset.info or {}).get("deleted"):
        return False
    try:
        return get_storage().exists(asset.storage_key)
    except (StorageError, OSError):
        return False


def reset_storage() -> None:
    global _storage
    with _lock:
        _storage = None


# Magic numbers of accepted media containers (cheap pre-check before the
# isolated media worker does full validation with hardened ffprobe).
_SIGNATURES = (
    (4, b"ftyp"),             # MP4/MOV/M4A/3GP
    (0, b"\x1a\x45\xdf\xa3"),  # Matroska/WebM
    (0, b"RIFF"),             # AVI/WAV
    (0, b"ID3"),              # MP3 with tag
    (0, b"\xff\xfb"), (0, b"\xff\xf3"), (0, b"\xff\xf2"),  # MP3 frames
    (0, b"OggS"),
    (0, b"fLaC"),
    (0, b"FLV"),
    (0, b"\x47"),             # MPEG-TS sync byte
    (0, b"\x30\x26\xb2\x75"),  # ASF/WMV
    (0, b"\xff\xf1"), (0, b"\xff\xf9"),  # AAC ADTS
)


def looks_like_media(path: Path) -> bool:
    with open(path, "rb") as handle:
        head = handle.read(64)
    return any(head[offset:offset + len(sig)] == sig for offset, sig in _SIGNATURES)
