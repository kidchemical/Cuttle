"""Settings persistence: one owner, scoped files, locked atomic updates.

Production files live in ``<home>/config/``; reads never create files.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import time

SCHEMA_VERSION = 1
MACHINE_KEYS = frozenset({"device_workers", "discovery"})
UI_KEYS = frozenset({"ui_layout", "shell_workspaces"})
RETIRED_KEYS = frozenset({
    "version", "default_pipeline", "factory_default_pipeline", "auto_start_default",
    "last_opened_pipeline", "recent_pipelines", "user_preferences", "sandbox",
    "pipeline_limits",
})
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _retry_sharing(operation):
    """Windows refuses to open or replace a file another process holds open.

    Readers are unlocked, so a read can meet another process's os.replace (or
    vice versa); antivirus scanners also hold freshly written files briefly.
    Back off up to ~10 s in total. POSIX never raises here.
    """
    delay, waited = 0.01, 0.0
    while True:
        try:
            return operation()
        except PermissionError:
            if os.name != "nt" or waited >= 10.0:
                raise
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 0.25)


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(_retry_sharing(lambda: path.read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise ValueError(f"Settings must be a JSON object: {path}")
    return value


def read_server(path: Path) -> dict:
    value = read_json(path)
    if "schema_version" in value and (
        type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError(f"Unsupported settings schema: {path}")
    return value


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _retry_sharing(lambda: os.replace(temporary, path))
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def settings_lock(path: Path):
    """Thread + process lock, on a stable sidecar rather than replaced JSON."""
    key = str(path.resolve())
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                # LK_LOCK retries only once a second and gives up after ten
                # tries, so a busy lock starves waiters; poll non-blocking.
                deadline = time.monotonic() + 30.0
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.025)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def clean_settings(value: dict) -> dict:
    out = {k: v for k, v in value.items() if k not in RETIRED_KEYS}
    if isinstance(out.get("channels"), dict):
        out["channels"] = {k: v for k, v in out["channels"].items() if k != "discord"}
    return out


class SettingsStorage:
    def __init__(self, server: Path, *, machine: Path, ui: Path):
        self.server, self.machine, self.ui = server, machine, ui
        self.lock = server.with_suffix(server.suffix + ".lock")

    def _path(self, key: str) -> Path:
        if key in MACHINE_KEYS:
            return self.machine
        if key in UI_KEYS:
            return self.ui
        return self.server

    def read(self) -> dict:
        out = clean_settings(read_server(self.server))
        # Only the declared keys can come from scoped files; unknown keys in
        # those files survive updates but cannot override server preferences.
        for path, keys in ((self.machine, MACHINE_KEYS), (self.ui, UI_KEYS)):
            out.update({k: v for k, v in read_json(path).items() if k in keys})
        return out

    def update(self, key: str, transform) -> dict:
        if key in RETIRED_KEYS or key == "schema_version":
            raise ValueError(f"Not a writable setting: {key}")
        with settings_lock(self.lock):
            path = self._path(key)
            document = read_server(path) if path == self.server else read_json(path)
            document[key] = transform(document.get(key))
            if key == "device_workers" and path == self.machine and isinstance(document[key], dict):
                workers = dict(document[key])
                if workers.pop("token", ""):
                    raise ValueError("Shared worker tokens are unsupported; pair each device through Host approval")
                document[key] = workers
            if path == self.server:
                document["schema_version"] = SCHEMA_VERSION
                document = clean_settings(document)
            atomic_write(path, document)
            return self.read()


def storage_for(server: Path | None = None) -> SettingsStorage:
    """Production storage, or an explicit path with its scoped files beside it (tests)."""
    if server is None:
        from core.runtime_paths import config_dir, settings_path

        folder, server = config_dir(), settings_path()
    else:
        folder = server.parent / (server.stem + ".d")
    return SettingsStorage(server, machine=folder / "machine_settings.json", ui=folder / "ui_state.json")
