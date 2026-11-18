"""Authorised live ingest and real-time processing (Part 31.8).

The owner's encoder (OBS etc.) sends the stream to CLIPFORGE over **SRT with a
per-session AES passphrase**. RTMP is intentionally not offered: FFmpeg's RTMP
listener logs an unexpected stream key but still accepts the publisher (verified
during development), so it cannot authenticate a feed.

* ``LiveIngest`` supervises an FFmpeg SRT listener that remuxes (no decoding)
  into MPEG-TS segments. When the publisher disconnects the listener is
  restarted and segment numbering continues, so a dropped connection inside the
  grace period is just a gap, not a new session.
* ``process_segment`` transcribes each completed segment and measures energy.
* ``rolling_candidates`` re-runs highlight scoring over a rolling window and
  marks the segments of strong candidates as *kept*.
* ``enforce_retention`` deletes segments older than the retention window unless
  they belong to a kept candidate — live data never grows without bound.
* ``build_replay`` concatenates the retained segments at end of stream.
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..config import get_settings
from . import ffmpeg_tools, transcription

log = logging.getLogger(__name__)

SEGMENT_PATTERN = "seg_%06d.ts"
#: An accepted connection that delivers no new bytes for this long is dropped.
STALL_S = 5.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:  # retention may delete old segments concurrently
        return 0


def new_passphrase() -> str:
    # SRT passphrases must be 10–79 characters.
    return secrets.token_urlsafe(24)


def port_range() -> list[int]:
    settings = get_settings()
    start = settings.live_srt_port
    return list(range(start, start + settings.live_srt_port_count))


def srt_listen_url(port: int, passphrase: str) -> str:
    host = get_settings().live_bind_host
    # timeout (µs): a vanished publisher is noticed in ~2.5 s, freeing the single
    # SRT listener for the encoder's reconnect.
    return f"srt://{host}:{port}?mode=listener&passphrase={passphrase}&pbkeylen=32&latency=300000&timeout=2500000"


def srt_publish_url(port: int, passphrase: str) -> str:
    host = get_settings().live_public_host
    return f"srt://{host}:{port}?passphrase={passphrase}&pbkeylen=32&latency=300000"


class LiveIngest:
    """One supervised SRT listener writing MPEG-TS segments."""

    def __init__(self, port: int, passphrase: str, directory: Path, *, segment_s: int,
                 on_segment: Callable[[int, Path, float], None], on_state: Callable[[str], None]):
        self.port = port
        self.passphrase = passphrase
        self.dir = directory
        self.segment_s = segment_s
        self.on_segment = on_segment
        self.on_state = on_state
        self.proc: subprocess.Popen | None = None
        self.connections = 0

    def _spawn(self, start_number: int) -> subprocess.Popen:
        self.dir.mkdir(parents=True, exist_ok=True)
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-i",
                srt_listen_url(self.port, self.passphrase), "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                "-f", "segment", "-segment_time", str(self.segment_s), "-reset_timestamps", "1",
                "-segment_start_number", str(start_number), "-segment_format", "mpegts",
                str(self.dir / SEGMENT_PATTERN)]
        # stderr goes to a per-spawn log file: an unread pipe could fill up and block FFmpeg.
        self._log = open(self.dir / f"listener_{start_number:06d}.log", "ab")  # noqa: SIM115 - closed when the listener exits
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=self._log, start_new_session=True)

    def _segment_files(self) -> list[tuple[int, Path]]:
        out = []
        for path in self.dir.glob("seg_*.ts"):
            try:
                out.append((int(path.stem.split("_")[1]), path))
            except (IndexError, ValueError):
                continue
        return sorted(out)

    def run(self, start_number: int, *, stop: Callable[[], bool], grace_s: float, max_session_s: float) -> int:
        """Run until stopped, the session limit is hit, or the publisher stays away
        longer than ``grace_s`` after disconnecting. Returns the next segment index."""
        next_index = start_number
        reported: set[int] = set()
        started = time.monotonic()
        disconnected_at: float | None = None
        quick_failures = 0
        self.on_state("waiting")

        def report(files: list[tuple[int, Path]]) -> None:
            for idx, path in files:
                if idx not in reported and _size(path) > 0:
                    reported.add(idx)
                    self.on_segment(idx, path, self._duration(path))

        while not stop() and time.monotonic() - started < max_session_s:
            spawned = time.monotonic()
            self.proc = self._spawn(next_index)
            connected = False
            stalled = False
            accepted_at: float | None = None
            last_bytes = 0
            last_growth = time.monotonic()
            while self.proc.poll() is None and not stop():
                now = time.monotonic()
                if not connected and disconnected_at is not None and now - disconnected_at > grace_s:
                    break  # the publisher did not come back in time
                if now - started > max_session_s:
                    break
                time.sleep(0.5)
                files = self._segment_files()
                current = [(idx, path) for idx, path in files if idx >= next_index]
                size = sum(_size(path) for _, path in current)
                now = time.monotonic()
                if current and accepted_at is None:
                    accepted_at = last_growth = now
                if size > last_bytes:
                    last_bytes, last_growth = size, now
                # "Connected" means media is actually arriving, not just an accepted socket.
                if size > 0 and not connected:
                    connected = True
                    self.connections += 1
                    disconnected_at = None
                    self.on_state("live")
                # FFmpeg's SRT listener serves exactly one connection. If an accepted
                # connection stops delivering data (half-open handshake, dead peer),
                # free the listener so the encoder's reconnect can get through.
                if accepted_at is not None and now - last_growth > STALL_S:
                    stalled = True
                    break
                report(files[:-1])  # a segment is complete once a newer one exists
            if self.proc.poll() is None:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=10)
            self._log.close()
            for idx, path in self._segment_files():
                if idx >= next_index and _size(path) == 0:
                    path.unlink(missing_ok=True)  # leftovers of a connection that sent nothing
            report(self._segment_files())  # flush the final segment
            next_index = (max(reported) + 1) if reported else next_index
            if stop():
                break
            if connected:
                disconnected_at = time.monotonic()
                self.on_state("reconnecting")
                continue
            if disconnected_at is not None and time.monotonic() - disconnected_at > grace_s:
                break
            if stalled:
                continue  # a dead connection, not a failing listener
            if time.monotonic() - spawned < 1.0:
                quick_failures += 1
                if quick_failures >= 5:
                    logs = sorted(self.dir.glob("listener_*.log"))
                    error = logs[-1].read_text(errors="ignore")[-400:] if logs else ""
                    raise RuntimeError(f"Live listener keeps failing to start: {error}")
                time.sleep(1.0)
        return next_index

    @staticmethod
    def _duration(path: Path) -> float:
        try:
            return max(0.0, ffmpeg_tools.probe(path).duration_s)
        except ffmpeg_tools.MediaError:
            return 0.0


def process_segment(path: Path, work: Path, offset_s: float) -> dict:
    """Transcribe one segment and measure its energy (absolute stream times)."""
    work.mkdir(parents=True, exist_ok=True)
    wav = work / (path.stem + ".wav")
    ffmpeg_tools.run(["-i", str(path), "-vn", "-ac", "1", "-ar", str(ffmpeg_tools.ASR_RATE), "-c:a", "pcm_s16le", str(wav)])
    duration = ffmpeg_tools.wav_duration(wav)
    levels = ffmpeg_tools.loudness_curve(wav, 0.5)
    words: list[dict] = []
    try:
        engine = transcription.select_engine("en")
        words = [w.to_dict() for w in engine.transcribe(wav, 0.0, duration, "en")]
    except transcription.TranscriptionUnavailable as exc:
        log.warning("Live transcription unavailable: %s", exc)
    wav.unlink(missing_ok=True)
    for word in words:
        word["start"] = round(word["start"] + offset_s, 3)
        word["end"] = round(word["end"] + offset_s, 3)
    energy = sum(levels) / len(levels) if levels else None
    return {"words": words, "energy": energy, "levels": levels, "duration": duration}


def build_replay(segments: list[Path], dest: Path) -> Path:
    """Concatenate retained segments (names we generated) into one MP4."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.with_suffix(".txt")
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in segments))
    ffmpeg_tools.run(["-f", "concat", "-safe", "0", "-protocol_whitelist", "file,pipe", "-i", str(listing),
                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                      "-movflags", "+faststart", str(dest)])
    listing.unlink(missing_ok=True)
    return dest


def run_in_thread(target: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread
