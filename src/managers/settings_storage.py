"""Settings persistence: one owner, scoped files, locked atomic updates.

Legacy installations stay in one file until the guarded cold-start migration.
The schema marker is committed last; reads never migrate or create files.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading

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


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
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
        os.replace(temporary, path)
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
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
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

    def _path(self, key: str, server: dict) -> Path:
        # Preserve the legacy generation until the offline migration. A new
        # installation has no legacy file and immediately uses scoped storage.
        if self.server.exists() and "schema_version" not in server:
            return self.server
        if key in MACHINE_KEYS:
            return self.machine
        if key in UI_KEYS:
            return self.ui
        return self.server

    def read(self) -> dict:
        server = read_server(self.server)
        if self.server.exists() and "schema_version" not in server:
            return clean_settings(server)
        out = clean_settings(server)
        # Only the declared keys can come from scoped files; unknown keys in
        # those files survive updates but cannot override server preferences.
        for path, keys in ((self.machine, MACHINE_KEYS), (self.ui, UI_KEYS)):
            out.update({k: v for k, v in read_json(path).items() if k in keys})
        return out

    def update(self, key: str, transform) -> dict:
        if key in RETIRED_KEYS or key == "schema_version":
            raise ValueError(f"Not a writable setting: {key}")
        with settings_lock(self.lock):
            server = read_server(self.server)
            legacy = self.server.exists() and "schema_version" not in server
            path = self._path(key, server)
            document = read_json(path)
            document[key] = transform(document.get(key))
            if key == "device_workers" and path == self.machine and isinstance(document[key], dict):
                workers = dict(document[key])
                if workers.pop("token", ""):
                    raise ValueError("Shared worker tokens belong in src/.env or the secrets directory")
                document[key] = workers
            if path == self.server:
                # Never mark legacy data as split merely because a key changed.
                if not legacy:
                    document["schema_version"] = SCHEMA_VERSION
                document = clean_settings(document)
            atomic_write(path, document)
            return self.read()


def storage_for(server: Path) -> SettingsStorage:
    # Explicit test/embedding paths keep all sibling files beside that path.
    # Production is resolved independently of the working directory.
    canonical = Path(__file__).resolve().parents[1] / "settings.json"
    folder = server.parent / "data" / "config" if server == canonical else server.parent / (server.stem + ".d")
    return SettingsStorage(server, machine=folder / "machine_settings.json", ui=folder / "ui_state.json")


def migration_needed(root: Path) -> bool:
    path = root / "src/settings.json"
    return path.exists() and "schema_version" not in read_json(path)


def migrate_settings(root: Path) -> list[tuple[Path, Path]]:
    """Offline only. Preserve active/unknown values; refuse destination conflicts."""
    server = root / "src/settings.json"
    folder = root / "src/data/config"
    storage = SettingsStorage(server, machine=folder / "machine_settings.json", ui=folder / "ui_state.json")
    with settings_lock(storage.lock):
        if not migration_needed(root):
            return []
        for path in (storage.machine, storage.ui):
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"Settings migration destination already exists: {path}")
        original = read_json(server)
        active = clean_settings(original)
        machine = {k: active.pop(k) for k in MACHINE_KEYS if k in active}
        ui = {k: active.pop(k) for k in UI_KEYS if k in active}
        workers = machine.get("device_workers") or {}
        if not isinstance(workers, dict):
            raise ValueError("device_workers must be a JSON object")
        workers = dict(workers)
        token = workers.pop("token", "")
        if not isinstance(token, str):
            raise ValueError("Legacy shared worker token must be a string")
        if "device_workers" in machine:
            machine["device_workers"] = workers
        from core.runtime_paths import secrets_dir
        secret_path = secrets_dir(root) / "worker_shared_token.json"
        if token and (secret_path.exists() or secret_path.is_symlink()):
            raise FileExistsError(f"Worker secret migration destination already exists: {secret_path}")
        active["schema_version"] = SCHEMA_VERSION
        created = []
        try:
            if token:
                atomic_write(secret_path, {"token": token})
                created.append(secret_path)
            for path, document in ((storage.machine, machine), (storage.ui, ui)):
                atomic_write(path, document)
                created.append(path)
            atomic_write(server, active)
        except (OSError, ValueError, TypeError):
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return [(server, storage.machine), (server, storage.ui)]
