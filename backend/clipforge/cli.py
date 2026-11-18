"""``clipforge`` command line: run services and perform admin tasks.

    clipforge api [--host 0.0.0.0] [--port 8000]
    clipforge worker [--queues default,media]
    clipforge migrate
    clipforge create-owner --email you@example.com      (password read from stdin/prompt)
    clipforge gen-key
    clipforge backup
    clipforge download-models
    clipforge doctor
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import shutil
import sys
from pathlib import Path


def _setup_logging() -> None:
    from .config import get_settings

    logging.basicConfig(level=get_settings().log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import get_settings

    settings = get_settings()
    if settings.is_production and not settings.resolved_master_keys():
        print("CLIPFORGE_MASTER_KEY is required in production (generate one with `clipforge gen-key`).", file=sys.stderr)
        return 2
    # Forwarded headers are interpreted by the app itself (see deps.client_ip and
    # CLIPFORGE_TRUSTED_PROXY_HOPS), never by uvicorn, so there is one source of truth.
    uvicorn.run("clipforge.api.app:create_app", factory=True, host=args.host, port=args.port,
                proxy_headers=False, log_level=settings.log_level.lower(), workers=args.workers)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    _setup_logging()
    from .jobs.worker import run_worker

    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    run_worker(queues)
    return 0


def cmd_migrate(_: argparse.Namespace) -> int:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "head")
    # LangGraph's checkpoint tables are created by the library itself.
    from .brain.runtime import get_checkpointer

    get_checkpointer()
    print("Database is up to date.")
    return 0


def cmd_create_owner(args: argparse.Namespace) -> int:
    from .db.base import session_scope
    from .security.passwords import WeakPasswordError
    from .services import auth

    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else getpass.getpass("Owner password: ")
    try:
        with session_scope() as db:
            user = auth.create_owner(db, email=args.email, password=password, display_name=args.name or "")
            print(f"Owner {user.email} created.")
    except (auth.AuthError, WeakPasswordError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def cmd_gen_key(_: argparse.Namespace) -> int:
    from .config import generate_master_key

    print(generate_master_key())
    return 0


def cmd_backup(_: argparse.Namespace) -> int:
    _setup_logging()
    from .handlers.maintenance_jobs import run_backup

    manifest = run_backup("manual")
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_download_models(args: argparse.Namespace) -> int:
    _setup_logging()
    from .media import models

    failed = 0
    if args.faster_whisper:
        from .config import get_settings

        settings = get_settings()
        target = settings.models_dir / "faster-whisper" / settings.faster_whisper_model
        try:
            from faster_whisper import download_model

            download_model(settings.faster_whisper_model, output_dir=str(target))
            print(f"ok    {'faster-whisper':16} {target}")
        except Exception as exc:  # noqa: BLE001 - network/hub errors are reported, not raised
            failed += 1
            print(f"FAIL  {'faster-whisper':16} {exc.__class__.__name__}: {str(exc)[:200]}", file=sys.stderr)
    if args.vosk or args.vosk_from:
        try:
            print(f"ok    {'vosk':16} {models.install_vosk(args.vosk_from)} (verified)")
        except models.ModelUnavailable as exc:
            failed += 1
            print(f"FAIL  {'vosk':16} {exc}", file=sys.stderr)
    for key in models.MODELS:
        try:
            path = models.ensure(key)
            print(f"ok    {key:16} {path}")
        except models.ModelUnavailable as exc:
            failed += 1
            print(f"FAIL  {key:16} {exc}", file=sys.stderr)
    from .media.transcription import engines_status

    engines = engines_status()
    for info in engines:
        print(f"asr   {info['engine']:16} {'available' if info['available'] else 'unavailable'} — {info['detail']}")
    if not any(info["available"] for info in engines):
        print("No speech-recognition model is installed: run `clipforge download-models --vosk` "
              "(or see docs/INSTALL.md for WhisperX).", file=sys.stderr)
        failed += 1
    return 1 if failed and args.strict else 0


def cmd_doctor(_: argparse.Namespace) -> int:
    """Checks a deployment without changing anything. Exit code 1 if something essential is missing."""
    from sqlalchemy import text

    from .config import get_settings
    from .db.base import session_scope
    from .media import ffmpeg_tools, models
    from .media.transcription import engines_status
    from .services.redis_client import get_redis

    settings = get_settings()
    results: list[tuple[str, bool, str, bool]] = []  # name, ok, detail, essential

    try:
        with session_scope() as db:
            version = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            vector = db.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one_or_none()
        results.append(("database", True, f"schema {version}", True))
        results.append(("pgvector", vector is not None, vector or "extension missing", True))
    except Exception as exc:  # noqa: BLE001 - report any connection problem
        results.append(("database", False, exc.__class__.__name__, True))
    results.append(("redis", get_redis() is not None, "optional: faster job wake-ups", False))
    ff = ffmpeg_tools.capabilities()
    results.append(("ffmpeg", bool(ff.get("available") and ff.get("libass")), ff.get("version", "missing"), True))
    keys = settings.resolved_master_keys()
    results.append(("master key", bool(keys), "configured" if settings.master_key else ("dev key" if keys else "missing"),
                    settings.is_production))
    for spec in models.status():
        results.append((f"model {spec['key']}", spec["available"], spec["description"], False))
    engines = engines_status()
    results.append(("speech recognition", any(e["available"] for e in engines),
                    ", ".join(f"{e['engine']}: {'ok' if e['available'] else 'no'}" for e in engines), True))
    usage = shutil.disk_usage(settings.data_dir)
    results.append(("disk", usage.free > 5 * 1024**3, f"{usage.free / 1024**3:.1f} GB free in {settings.data_dir}", False))
    results.append(("google app", bool(settings.google_client_id and settings.google_client_secret),
                    "needed for YouTube publishing", False))
    results.append(("meta app", bool(settings.meta_app_id and settings.meta_app_secret),
                    "needed for Instagram publishing", False))
    from urllib.parse import urlsplit

    public = urlsplit(settings.public_base_url)
    local_only = public.hostname in ("localhost", "127.0.0.1", "::1")
    if public.scheme != "https":
        results.append(("public url", local_only,
                        f"{settings.public_base_url}: fine on this machine only; use https:// for access from other devices "
                        "(OAuth sign-in and secure cookies need it)" if local_only else
                        f"{settings.public_base_url} is plain HTTP on a network address: set up HTTPS (docs/DEPLOYMENT.md)",
                        settings.is_production and not local_only))

    width = max(len(r[0]) for r in results)
    for name, ok, detail, essential in results:
        mark = "ok  " if ok else ("FAIL" if essential else "warn")
        print(f"{mark}  {name:<{width}}  {detail}")
    return 1 if any(not ok and essential for _, ok, _, essential in results) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipforge", description="CLIPFORGE AI service and admin commands")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("api", help="run the HTTP API")
    p.add_argument("--host", default=os.environ.get("CLIPFORGE_API_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("CLIPFORGE_API_PORT", "8000")))
    p.add_argument("--workers", type=int, default=1)
    p.set_defaults(func=cmd_api)

    p = sub.add_parser("worker", help="run a job worker")
    p.add_argument("--queues", default=os.environ.get("CLIPFORGE_WORKER_QUEUES", "default,media"))
    p.set_defaults(func=cmd_worker)

    sub.add_parser("migrate", help="create/upgrade the database schema").set_defaults(func=cmd_migrate)

    p = sub.add_parser("create-owner", help="create the owner account (first run)")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    p.set_defaults(func=cmd_create_owner)

    sub.add_parser("gen-key", help="print a new random master encryption key").set_defaults(func=cmd_gen_key)
    sub.add_parser("backup", help="back up the database and memory now").set_defaults(func=cmd_backup)
    p = sub.add_parser("download-models", help="download and verify the local AI models")
    p.add_argument("--strict", action="store_true", help="exit non-zero if any model is missing")
    p.add_argument("--vosk", action="store_true", help="also install the English speech model (~1.8 GB download)")
    p.add_argument("--vosk-from", metavar="PATH", help="install the speech model from a local .zip or folder")
    p.add_argument("--faster-whisper", action="store_true",
                   help="also download the faster-whisper model (CLIPFORGE_FASTER_WHISPER_MODEL, from Hugging Face)")
    p.set_defaults(func=cmd_download_models)
    sub.add_parser("doctor", help="check that this installation is complete").set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
