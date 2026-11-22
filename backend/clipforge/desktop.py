"""CLIPFORGE as an ordinary Windows app: no Docker, no Linux.

Everything runs as normal programs on this PC, listening on 127.0.0.1 only:

* PostgreSQL 16 with pgvector (from the ``pgserver`` package),
* the API, the two job workers (``default`` and ``media``) and the web app (Node.js).

The installation folder (``--root``) holds::

    app/backend      CLIPFORGE's Python code        app/web     the built web app (server.js)
    runtime/venv     Python and its packages        data/       videos, models, the database
    config.env       generated settings and keys    logs/       one log per part

Commands (the Windows launcher and setup use them; they also work on Linux and macOS)::

    clipforge desktop init    create config.env, the database and its tables (setup runs this)
    clipforge desktop run     start everything and keep it running until `stop`
    clipforge desktop stop    stop everything
    clipforge desktop status  what is running
    clipforge desktop models  download the AI models (--vosk adds the 1.8 GB speech model)
"""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if WINDOWS else 0  # CREATE_NO_WINDOW: no console windows popping up

DEFAULT_PORTS = {"CLIPFORGE_WEB_PORT": "3000", "CLIPFORGE_API_PORT": "38100", "CLIPFORGE_DB_PORT": "38101"}
#: The parts started after the database, in order, and their command lines (``{python}`` and
#: ``{node}`` are filled in).
SERVICES = ("api", "worker", "media-worker", "web")


def _say(message: str) -> None:
    print(message, flush=True)


def _log(layout: Layout, message: str) -> None:
    with (layout.logs / "desktop.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


# ----------------------------------------------------------------------------- layout & config
@dataclass
class Layout:
    root: Path

    @property
    def backend(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def web(self) -> Path:
        return self.root / "app" / "web"

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def pgdata(self) -> Path:
        return self.data / "postgres"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def run(self) -> Path:
        return self.root / "run"

    @property
    def config(self) -> Path:
        return self.root / "config.env"

    def ensure(self) -> None:
        for path in (self.data, self.logs, self.run):
            path.mkdir(parents=True, exist_ok=True)


def read_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def ensure_config(layout: Layout) -> dict[str, str]:
    """config.env: created once with fresh secrets; later runs keep (and only add to) it."""
    from .config import generate_master_key

    values = read_config(layout.config)
    added = {}
    if not values.get("CLIPFORGE_MASTER_KEY"):
        added["CLIPFORGE_MASTER_KEY"] = generate_master_key()
    if not values.get("CLIPFORGE_DB_PASSWORD"):
        added["CLIPFORGE_DB_PASSWORD"] = secrets.token_urlsafe(24)
    for key, value in DEFAULT_PORTS.items():
        if not values.get(key):
            added[key] = value
    if added:
        layout.root.mkdir(parents=True, exist_ok=True)
        header = "" if layout.config.exists() else (
            "# CLIPFORGE settings for this PC. Keep this file private: it holds the key that\n"
            "# encrypts your connected accounts. Other CLIPFORGE_* settings may be added below.\n")
        with layout.config.open("a", encoding="utf-8") as handle:
            handle.write(header + "".join(f"{key}={value}\n" for key, value in added.items()))
        values.update(added)
        _restrict_to_user(layout.config)
    return values


def _restrict_to_user(path: Path) -> None:
    """Only the signed-in user may read the settings file (it holds the encryption key)."""
    if WINDOWS:
        user = os.environ.get("USERNAME")
        if user:
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                           capture_output=True, creationflags=NO_WINDOW, check=False)
    else:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def videos_folder() -> Path:
    """The user's Videos folder, wherever Windows keeps it (moved to D:, OneDrive, ...)."""
    if WINDOWS:
        with contextlib.suppress(Exception):
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                raw, _ = winreg.QueryValueEx(key, "My Video")
                return Path(os.path.expandvars(raw))
    return Path.home() / "Videos"


def environment(layout: Layout, config: dict[str, str]) -> dict[str, str]:
    """Settings every part runs with."""
    web_port, db_port = config["CLIPFORGE_WEB_PORT"], config["CLIPFORGE_DB_PORT"]
    inbox = videos_folder() / "ClipForge"
    env = dict(os.environ)
    env.update({key: value for key, value in config.items() if key.startswith("CLIPFORGE_")})
    env.update({
        "CLIPFORGE_ENV": "production",
        "CLIPFORGE_LOCAL_MODE": "true",  # one person on this PC: no sign-in
        "CLIPFORGE_DATA_DIR": str(layout.data),
        "CLIPFORGE_DATABASE_URL": f"postgresql+psycopg://clipforge:{config['CLIPFORGE_DB_PASSWORD']}@127.0.0.1:{db_port}/clipforge",
        "CLIPFORGE_REDIS_URL": config.get("CLIPFORGE_REDIS_URL", "redis://127.0.0.1:1/0"),  # none: the database is the queue
        "CLIPFORGE_PUBLIC_BASE_URL": f"http://localhost:{web_port}",
        "CLIPFORGE_ALLOWED_ORIGINS": f"http://localhost:{web_port},http://127.0.0.1:{web_port}",
        "CLIPFORGE_COOKIE_SECURE": "false",  # plain http on this PC only
        "CLIPFORGE_TRUSTED_PROXY_HOPS": "1",  # the web app forwards to the API
        "CLIPFORGE_INBOX_DIR": str(inbox),
        "CLIPFORGE_INBOX_DISPLAY": str(inbox),
        "CLIPFORGE_LIVE_BIND_HOST": "127.0.0.1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("CLIPFORGE_DB_PASSWORD", None)
    tools = [str(p) for p in (_ffmpeg_dir(),) if p]
    if tools:
        env["PATH"] = os.pathsep.join([*tools, env.get("PATH", "")])
    return env


def _ffmpeg_dir() -> Path | None:
    with contextlib.suppress(Exception):
        import ffmpeg  # the ffmpeg-binaries package (Windows)

        folder = getattr(ffmpeg, "FFMPEG_FOLDER", None)
        if folder:
            return Path(folder)
    return None


def node_binary() -> str | None:
    with contextlib.suppress(Exception):
        import nodejs_wheel

        base = Path(nodejs_wheel.__file__).parent
        candidate = base / "node.exe" if WINDOWS else base / "bin" / "node"
        if candidate.exists():
            return str(candidate)
    return shutil.which("node")


def pg_bin() -> Path:
    import pgserver  # PostgreSQL 16 + pgvector, as a package

    return Path(pgserver.__file__).parent / "pginstall" / "bin"


def ensure_timezones(pg_share: Path | None = None) -> bool:
    """PostgreSQL needs its time-zone database (``share/postgresql/timezone``). The Windows build in
    pgserver ships without it (on Linux it uses the system's), so it is copied in from the
    ``tzdata`` package: the same standard IANA files. Returns True if it copied them."""
    target = (pg_share or (pg_bin().parent / "share" / "postgresql")) / "timezone"
    if target.is_dir() and (target / "UTC").exists():
        return False
    if pg_share is None and not WINDOWS:
        return False
    import tzdata

    source = Path(tzdata.__file__).parent / "zoneinfo"
    shutil.copytree(source, target, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__init__.py", "__pycache__", "*.zi", "*.tab", "zones", "leapseconds"))
    return True


def _exe(name: str) -> str:
    return str(pg_bin() / (f"{name}.exe" if WINDOWS else name))


# ----------------------------------------------------------------------------- PostgreSQL
def _run(args: list[str], log: Path, env: dict[str, str] | None = None, timeout: float = 600) -> int:
    with log.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"\n$ {' '.join(args)}\n")
        handle.flush()
        return subprocess.run(args, stdout=handle, stderr=subprocess.STDOUT, env=env, timeout=timeout,
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False).returncode


def database_running(layout: Layout) -> bool:
    if not (layout.pgdata / "PG_VERSION").exists():
        return False
    return subprocess.run([_exe("pg_ctl"), "status", "-D", str(layout.pgdata)], capture_output=True,
                          stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False).returncode == 0


def start_database(layout: Layout, config: dict[str, str]) -> None:
    log = layout.logs / "postgres.log"
    if ensure_timezones():
        _log(layout, "installed PostgreSQL's time-zone data")
    if not (layout.pgdata / "PG_VERSION").exists():
        _say("Creating CLIPFORGE's database")
        layout.pgdata.parent.mkdir(parents=True, exist_ok=True)
        pwfile = layout.run / "pw.tmp"
        pwfile.write_text(config["CLIPFORGE_DB_PASSWORD"], encoding="utf-8")
        try:
            code = _run([_exe("initdb"), "-D", str(layout.pgdata), "-U", "clipforge", "--pwfile", str(pwfile),
                         "-A", "scram-sha-256", "-E", "UTF8", "--no-locale"], log)
        finally:
            pwfile.unlink(missing_ok=True)
        if code != 0:
            raise RuntimeError(f"Could not create the database (initdb exit code {code}); see {log}")
    if database_running(layout):
        return
    options = (f"-p {config['CLIPFORGE_DB_PORT']} -c listen_addresses=127.0.0.1 -c unix_socket_directories="
               " -c max_connections=60 -c shared_buffers=128MB")
    code = _run([_exe("pg_ctl"), "start", "-D", str(layout.pgdata), "-w", "-t", "120",
                 "-l", str(layout.logs / "postgres-server.log"), "-o", options], log)
    if code != 0:
        raise RuntimeError(f"The database did not start (pg_ctl exit code {code}); see {layout.logs / 'postgres-server.log'}")


def stop_database(layout: Layout) -> None:
    if database_running(layout):
        _run([_exe("pg_ctl"), "stop", "-D", str(layout.pgdata), "-m", "fast", "-w", "-t", "60"],
             layout.logs / "postgres.log")


def create_database(config: dict[str, str]) -> None:
    import psycopg

    dsn = f"postgresql://clipforge:{config['CLIPFORGE_DB_PASSWORD']}@127.0.0.1:{config['CLIPFORGE_DB_PORT']}/postgres"
    with psycopg.connect(dsn, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = 'clipforge'").fetchone() is None:
            conn.execute("CREATE DATABASE clipforge")


def _cli(*args: str) -> list[str]:
    return [sys.executable, "-m", "clipforge.cli", *args]


def migrate(layout: Layout, env: dict[str, str]) -> None:
    code = _run(_cli("migrate"), layout.logs / "migrate.log", env=env)
    if code != 0:
        raise RuntimeError(f"Updating the database failed; see {layout.logs / 'migrate.log'}")


# ----------------------------------------------------------------------------- the parts
def service_command(name: str, config: dict[str, str], layout: Layout) -> tuple[list[str], dict[str, str]]:
    extra: dict[str, str] = {}
    if name == "api":
        return _cli("api", "--host", "127.0.0.1", "--port", config["CLIPFORGE_API_PORT"]), extra
    if name == "worker":
        return _cli("worker", "--queues", "default"), extra
    if name == "media-worker":
        return _cli("worker", "--queues", "media"), extra
    node = node_binary()
    if not node:
        raise RuntimeError("Node.js is missing (the nodejs-wheel-binaries package)")
    extra = {"PORT": config["CLIPFORGE_WEB_PORT"], "HOSTNAME": "127.0.0.1", "NODE_ENV": "production",
             "CLIPFORGE_API_URL": f"http://127.0.0.1:{config['CLIPFORGE_API_PORT']}",
             "NEXT_TELEMETRY_DISABLED": "1"}
    return [node, str(layout.web / "server.js")], extra


class Part:
    def __init__(self, name: str, layout: Layout, config: dict[str, str], env: dict[str, str]):
        self.name, self.layout = name, layout
        self.args, extra = service_command(name, config, layout)
        self.env = {**env, **extra}
        self.proc: subprocess.Popen | None = None
        self.starts: list[float] = []

    def start(self) -> None:
        log = (self.layout.logs / f"{self.name}.log").open("a", encoding="utf-8", errors="replace")
        log.write(f"\n===== started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log.flush()
        cwd = str(self.layout.web) if self.name == "web" else None
        self.proc = subprocess.Popen(self.args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=self.env, cwd=cwd, creationflags=NO_WINDOW)
        log.close()  # the child keeps its own handle
        self.starts.append(time.monotonic())

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def may_restart(self) -> bool:
        recent = [t for t in self.starts if time.monotonic() - t < 600]
        return len(recent) < 5  # five crashes in ten minutes: stop trying, the log says why

    def stop(self) -> None:
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)


# ----------------------------------------------------------------------------- commands
def _lock(layout: Layout):
    """Only one `desktop run` per installation. Returns an open file that holds the lock, or None."""
    handle = (layout.run / "desktop.lock").open("a+")
    try:
        if WINDOWS:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def ready(config: dict[str, str], timeout: float = 2) -> bool:
    url = f"http://127.0.0.1:{config['CLIPFORGE_WEB_PORT']}/api/health/ready"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # a fixed local address
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def cmd_init(layout: Layout) -> int:
    layout.ensure()
    config = ensure_config(layout)
    env = environment(layout, config)
    start_database(layout, config)
    try:
        create_database(config)
        _say("Preparing the database tables")
        migrate(layout, env)
    finally:
        stop_database(layout)
    (videos_folder() / "ClipForge").mkdir(parents=True, exist_ok=True)
    _say("CLIPFORGE is set up")
    return 0


def cmd_run(layout: Layout, *, idle_exit: float | None = None) -> int:
    layout.ensure()
    lock = _lock(layout)
    if lock is None:
        _say("CLIPFORGE is already running")
        return 0
    stop_flag = layout.run / "stop"
    stop_flag.unlink(missing_ok=True)
    config = ensure_config(layout)
    env = environment(layout, config)
    status = layout.run / "status.txt"
    parts: list[Part] = []
    try:
        status.write_text("starting database", encoding="utf-8")
        start_database(layout, config)
        create_database(config)
        status.write_text("updating database", encoding="utf-8")
        migrate(layout, env)
        names = [n for n in SERVICES if n != "web" or (layout.web / "server.js").exists()]
        if "web" not in names:
            _log(layout, f"the web app is missing ({layout.web / 'server.js'}); run CLIPFORGE setup again")
        parts = [Part(name, layout, config, env) for name in names]
        for part in parts:
            part.start()
        status.write_text("running", encoding="utf-8")
        started = time.monotonic()
        while not stop_flag.exists():
            for part in parts:
                if not part.running() and part.may_restart():
                    _log(layout, f"{part.name} stopped (exit {part.proc.returncode}); restarting")
                    part.start()
            if idle_exit is not None and time.monotonic() - started > idle_exit:
                break
            time.sleep(1)
        return 0
    except Exception as exc:  # noqa: BLE001
        status.write_text(f"failed: {exc}", encoding="utf-8")
        _say(f"CLIPFORGE could not start: {exc}")
        return 1
    finally:
        for part in reversed(parts):
            part.stop()
        stop_database(layout)
        stop_flag.unlink(missing_ok=True)
        if status.exists() and status.read_text(encoding="utf-8") == "running":
            status.write_text("stopped", encoding="utf-8")
        lock.close()


def cmd_stop(layout: Layout, wait: float = 90) -> int:
    layout.run.mkdir(parents=True, exist_ok=True)
    probe = _lock(layout)
    if probe is not None:  # nothing running
        probe.close()
        if (layout.pgdata / "PG_VERSION").exists():
            stop_database(layout)
        _say("CLIPFORGE is not running")
        return 0
    (layout.run / "stop").write_text("stop", encoding="utf-8")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        probe = _lock(layout)
        if probe is not None:
            probe.close()
            _say("CLIPFORGE stopped")
            return 0
        time.sleep(1)
    _say("CLIPFORGE did not stop in time")
    return 1


def cmd_status(layout: Layout) -> int:
    config = ensure_config(layout)
    state = (layout.run / "status.txt").read_text(encoding="utf-8") if (layout.run / "status.txt").exists() else "never started"
    _say(f"CLIPFORGE: {state}; web app {'answers' if ready(config) else 'not answering'} on http://localhost:{config['CLIPFORGE_WEB_PORT']}")
    return 0 if ready(config) else 3


def cmd_models(layout: Layout, vosk: bool) -> int:
    config = ensure_config(layout)
    env = environment(layout, config)
    code = subprocess.run(_cli("download-models"), env=env, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False).returncode
    if vosk and code == 0:
        code = subprocess.run(_cli("download-models", "--vosk"), env=env, stdin=subprocess.DEVNULL,
                              creationflags=NO_WINDOW, check=False).returncode
    return code


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="clipforge desktop")
    parser.add_argument("action", choices=["init", "run", "stop", "status", "models"])
    parser.add_argument("--root", required=True, type=Path, help="the installation folder")
    parser.add_argument("--vosk", action="store_true", help="models: also the 1.8 GB speech model")
    parser.add_argument("--idle-exit", type=float, default=None, help=argparse.SUPPRESS)  # tests
    args = parser.parse_args(argv)
    layout = Layout(args.root.resolve())
    if args.action == "init":
        return cmd_init(layout)
    if args.action == "run":
        return cmd_run(layout, idle_exit=args.idle_exit)
    if args.action == "stop":
        return cmd_stop(layout)
    if args.action == "status":
        return cmd_status(layout)
    return cmd_models(layout, args.vosk)
