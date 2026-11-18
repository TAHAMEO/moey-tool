"""Safe URL ingestion: SSRF-resistant validation and download (Part 22).

* Only ``http``/``https``; no credentials in URLs; restricted ports.
* Every hostname is resolved and **all** addresses must be globally routable —
  loopback, private, link-local (incl. cloud metadata 169.254.169.254),
  CGNAT, multicast, reserved and IPv4-mapped forms are rejected.
* The connection is pinned to the validated IP (Host header + TLS SNI keep the
  original name), which defeats DNS-rebinding between check and use.
* Redirects are followed manually, re-validating every hop, with a hop limit.
* Size, time and content-type limits are enforced while streaming.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..config import get_settings

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443, 8080, 8443}
BLOCKED_HOST_SUFFIXES = (".local", ".internal", ".localhost", ".localdomain", ".home.arpa", ".lan")
BLOCKED_HOSTS = {"localhost", "metadata", "metadata.google.internal", "instance-data"}
MEDIA_CONTENT_TYPES = (
    "video/",
    "audio/",
    "application/octet-stream",
    "binary/octet-stream",
    "application/mp4",
    "application/x-matroska",
)
USER_AGENT = "CLIPFORGE-AI/0.1 (+media-ingest)"


class UnsafeURLError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class ValidatedTarget:
    url: str
    scheme: str
    host: str
    port: int
    addresses: list[str]
    allowlisted: bool = False


def _is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_public_ip(str(ip.ipv4_mapped))
        if ip.sixtofour is not None:
            return _is_public_ip(str(ip.sixtofour))
        if ip.teredo is not None:
            return False
    return bool(
        ip.is_global
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
        and not ip.is_unspecified
    )


def _resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError("dns_failure", f"Could not resolve host {host!r}") from exc
    addresses: list[str] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            addresses.append(sockaddr[0].split("%", 1)[0])
    if not addresses:
        raise UnsafeURLError("dns_failure", f"No addresses for host {host!r}")
    return list(dict.fromkeys(addresses))


def validate_url(url: str, *, allow_private_hosts: list[str] | None = None) -> ValidatedTarget:
    if not isinstance(url, str) or len(url) > 4096:
        raise UnsafeURLError("invalid", "URL is missing or too long")
    if any(ord(ch) < 0x20 for ch in url):
        raise UnsafeURLError("invalid", "URL contains control characters")
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError("scheme", f"Only http and https links are accepted (got {scheme or 'none'!r})")
    if parts.username or parts.password:
        raise UnsafeURLError("credentials", "Links with embedded credentials are not accepted")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise UnsafeURLError("invalid", "URL has no host")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURLError("invalid", "Host name is not valid") from exc
    try:
        port = parts.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise UnsafeURLError("invalid", "Invalid port") from exc

    settings = get_settings()
    allowlist = set(allow_private_hosts or [])
    if not settings.is_production:
        allowlist |= set(settings.ssrf_private_allowlist)
    allowlisted = host in allowlist

    if not allowlisted:
        if port not in ALLOWED_PORTS:
            raise UnsafeURLError("port", f"Port {port} is not allowed")
        if host in BLOCKED_HOSTS or host.endswith(BLOCKED_HOST_SUFFIXES):
            raise UnsafeURLError("blocked_host", "Internal host names are not allowed")

    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addresses = [str(literal)]
    except ValueError:
        addresses = _resolve(host, port)

    if not allowlisted:
        for address in addresses:
            if not _is_public_ip(address):
                raise UnsafeURLError(
                    "private_address", "Links to private, local or internal network addresses are blocked"
                )

    normalized = urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))
    return ValidatedTarget(normalized, scheme, host, port, addresses, allowlisted)


@dataclass
class FetchResult:
    path: Path
    size: int
    content_type: str
    final_url: str
    sha256: str
    redirects: int


def _pinned_request_args(target: ValidatedTarget) -> tuple[str, dict, dict]:
    parts = urlsplit(target.url)
    ip = target.addresses[0]
    ip_host = f"[{ip}]" if ":" in ip else ip
    default_port = 443 if target.scheme == "https" else 80
    netloc = ip_host if target.port == default_port else f"{ip_host}:{target.port}"
    pinned_url = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    host_header = target.host if target.port == default_port else f"{target.host}:{target.port}"
    headers = {"Host": host_header, "User-Agent": USER_AGENT, "Accept": "*/*"}
    extensions = {"sni_hostname": target.host} if target.scheme == "https" else {}
    return pinned_url, headers, extensions


def safe_download(
    url: str,
    dest: Path,
    *,
    max_bytes: int | None = None,
    timeout_s: float = 600.0,
    max_redirects: int = 5,
    allowed_content_types: tuple[str, ...] = MEDIA_CONTENT_TYPES,
    allow_private_hosts: list[str] | None = None,
    extra_headers: dict[str, str] | None = None,
    allowed_redirect_hosts: set[str] | None = None,
) -> FetchResult:
    """Download ``url`` to ``dest`` under SSRF, size, time and type limits."""
    settings = get_settings()
    max_bytes = max_bytes or settings.max_download_mb * 1024 * 1024
    deadline = time.monotonic() + timeout_s
    current = url
    redirects = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    with httpx.Client(timeout=httpx.Timeout(30.0, connect=15.0), follow_redirects=False, trust_env=True) as client:
        while True:
            target = validate_url(current, allow_private_hosts=allow_private_hosts)
            if redirects and allowed_redirect_hosts is not None and target.host not in allowed_redirect_hosts:
                raise FetchError("redirect_host", f"Redirect to unexpected host {target.host!r} refused")
            pinned_url, headers, extensions = _pinned_request_args(target)
            if extra_headers:
                headers.update(extra_headers)
            with client.stream("GET", pinned_url, headers=headers, extensions=extensions) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("redirect", "Redirect without location", status=response.status_code)
                    redirects += 1
                    if redirects > max_redirects:
                        raise FetchError("too_many_redirects", "Too many redirects")
                    current = urljoin(target.url, location)
                    continue
                if response.status_code in (401, 403):
                    raise FetchError("access_denied", "The server refused access to this file", status=response.status_code)
                if response.status_code == 404:
                    raise FetchError("not_found", "The file was not found", status=404)
                if response.status_code >= 400:
                    raise FetchError("http_error", f"Server answered HTTP {response.status_code}", status=response.status_code)

                content_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip().lower()
                if allowed_content_types and not content_type.startswith(allowed_content_types):
                    raise FetchError("content_type", f"Link does not point to a media file (content type {content_type})")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise FetchError("too_large", "File exceeds the configured download limit")

                digest = hashlib.sha256()
                size = 0
                with open(tmp, "wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise FetchError("too_large", "File exceeds the configured download limit")
                        if time.monotonic() > deadline:
                            raise FetchError("timeout", "Download took too long")
                        digest.update(chunk)
                        handle.write(chunk)
                tmp.replace(dest)
                return FetchResult(dest, size, content_type, target.url, digest.hexdigest(), redirects)


def safe_head(url: str, *, allow_private_hosts: list[str] | None = None, timeout_s: float = 15.0) -> dict:
    """Validate and probe a URL without downloading the body."""
    target = validate_url(url, allow_private_hosts=allow_private_hosts)
    pinned_url, headers, extensions = _pinned_request_args(target)
    with httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=True) as client:
        response = client.head(pinned_url, headers=headers, extensions=extensions)
        if response.status_code in (405, 501):
            headers["Range"] = "bytes=0-0"
            response = client.get(pinned_url, headers=headers, extensions=extensions)
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", "").split(";")[0].strip().lower(),
        "content_length": response.headers.get("content-length"),
        "location": response.headers.get("location"),
        "host": target.host,
    }
