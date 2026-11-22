"""Redis access with graceful degradation.

Redis is an accelerator (wake-ups, rate limiting, progress fan-out), never the
source of truth: if it is unreachable, callers fall back to Postgres polling
and in-process counters instead of failing.
"""

from __future__ import annotations

import logging
import threading
import time

import redis

from ..config import get_settings

log = logging.getLogger(__name__)

_client: redis.Redis | None = None
_lock = threading.Lock()
_down_until = 0.0
_local_counters: dict[str, tuple[int, float]] = {}


def get_redis() -> redis.Redis | None:
    global _client, _down_until
    if get_settings().redis_url.strip().lower() in ("", "none"):  # no Redis (the Windows app): the database does the work
        return None
    if time.monotonic() < _down_until:
        return None
    with _lock:
        if _client is None:
            _client = redis.Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2,
                                           decode_responses=True)
    try:
        _client.ping()
        return _client
    except redis.RedisError as exc:
        log.warning("Redis unavailable (%s); degrading to database polling", exc)
        _down_until = time.monotonic() + 15
        return None


def incr_window(key: str, window_s: int) -> int:
    """Increment a counter that expires ``window_s`` after its first hit."""
    client = get_redis()
    if client is not None:
        try:
            pipe = client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_s, nx=True)
            count, _ = pipe.execute()
            return int(count)
        except redis.RedisError:
            pass
    now = time.monotonic()
    count, expires = _local_counters.get(key, (0, now + window_s))
    if now > expires:
        count, expires = 0, now + window_s
    count += 1
    _local_counters[key] = (count, expires)
    return count


def get_count(key: str) -> int:
    client = get_redis()
    if client is not None:
        try:
            return int(client.get(key) or 0)
        except redis.RedisError:
            pass
    count, expires = _local_counters.get(key, (0, 0.0))
    return count if time.monotonic() <= expires else 0


def reset(key: str) -> None:
    client = get_redis()
    if client is not None:
        try:
            client.delete(key)
        except redis.RedisError:
            pass
    _local_counters.pop(key, None)


def publish(channel: str, message: str) -> None:
    client = get_redis()
    if client is not None:
        try:
            client.publish(channel, message)
            client.lpush(f"{channel}:signal", "1")
            client.ltrim(f"{channel}:signal", 0, 50)
        except redis.RedisError:
            pass


def wait_signal(channels: str | list[str], timeout_s: float) -> bool:
    """Block until a wake-up signal arrives on any channel (or timeout). False if Redis is down."""
    client = get_redis()
    if client is None:
        time.sleep(min(timeout_s, 2.0))
        return False
    keys = [f"{c}:signal" for c in ([channels] if isinstance(channels, str) else channels)]
    try:
        result = client.blpop(keys, timeout=max(1, int(timeout_s)))
        return result is not None
    except redis.RedisError:
        time.sleep(min(timeout_s, 2.0))
        return False
