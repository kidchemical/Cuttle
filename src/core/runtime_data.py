"""Move runtime state out of the install tree: PYTHONPATH=src python -m core.runtime_data.

The install tree (checkout, ``Program Files``, AppImage mount) is code only;
every mutable byte belongs in the per-user Cuttle home
(``core.runtime_paths.cuttle_home``). This moves an older checkout's state
there once, at daemon cold start, while nothing holds the files open.
"""
from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path

from core.runtime_paths import cuttle_home


def _install_sources(root: Path, home: Path) -> list[tuple[Path, Path]]:
    """Checkout-relative state locations and where each lives in the home."""
    return [
        (root / "src/data", home),
        (root / "src/settings.json", home / "config/settings.json"),
        (root / "src/output", home / "output"),
        (root / "src/web/logs", home / "logs/queries"),
        (root / "src/.env", home / ".env"),
        (root / ".cuttle/personal/secrets", home / "secrets"),
        (root / ".cuttle/personal/path-aliases.json", home / "personal/path-aliases.json"),
        (root / ".cuttle_global/personal", home / "personal"),
    ]


def _files(path: Path) -> list[Path]:
    if path.is_file() or path.is_symlink():
        return [path]
    if not path.is_dir():
        return []
    return sorted(p for p in path.rglob("*") if p.is_file() or p.is_symlink())


def _legacy_logs(home: Path) -> list[tuple[Path, Path]]:
    """``~/cuttle_logs`` predates the home. Only the real default home adopts it:
    an explicit ``CUTTLE_HOME`` (tests, shadows, a second checkout) never does."""
    if (os.environ.get("CUTTLE_HOME") or "").strip():
        return []
    return [(Path.home() / "cuttle_logs", home / "logs")]


def migration_pairs(root: Path, home: Path | None = None) -> list[tuple[Path, Path]]:
    """Sources that still hold state (empty directories do not count)."""
    sources = _install_sources(root, cuttle_home() if home is None else home)
    if home is None:
        sources += _legacy_logs(cuttle_home())
    return [(source, target) for source, target in sources if _files(source)]


def pending_install_state(root: Path) -> list[Path]:
    """Install-tree state a host must not run beside (old logs never block)."""
    return [source for source, _ in migration_pairs(root) if source.is_relative_to(root)]


def _file_moves(source: Path, target: Path) -> list[tuple[Path, Path]]:
    if source.is_file() or source.is_symlink():
        return [(source, target)]
    return [(path, target / path.relative_to(source)) for path in _files(source)]


def _prune_empty_dirs(path: Path) -> None:
    if not path.is_dir() or path.is_symlink():
        return
    for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if child.is_dir() and not child.is_symlink() and not any(child.iterdir()):
            child.rmdir()
    if not any(path.iterdir()):
        path.rmdir()


_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _displaced(moves: list[tuple[Path, Path]], stamp: str) -> list[tuple[Path, Path]]:
    """Home files the move would collide with, and where each is set aside.

    Short-lived helpers (harness CLIs) run fresh code and can write the home
    before the long-lived host restarts. The install tree's copy is the
    authoritative one; the newer stray is kept beside it, never overwritten.
    A migrated database must not pair with a stray WAL, so orphan sidecars
    of an incoming database are set aside too.
    """
    targets = {target for _, target in moves}
    clashes = {target for target in targets if target.exists() or target.is_symlink()}
    for target in targets:
        for suffix in _SQLITE_SIDECARS:
            sidecar = target.with_name(target.name + suffix)
            if sidecar not in targets and (sidecar.exists() or sidecar.is_symlink()):
                clashes.add(sidecar)
    return [(path, path.with_name(f"{path.name}.pre-migration-{stamp}")) for path in sorted(clashes)]


def migrate(root: Path, home: Path | None = None) -> list[tuple[Path, Path]]:
    """Caller must ensure all Cuttle processes using this checkout are stopped.

    Never overwrites a file: a colliding home file is renamed aside first.
    Directories merge file by file (the home may already hold owner folders),
    so SQLite sidecars travel with their database. Rolls back on failure.
    """
    pairs = migration_pairs(root, home)
    moves = [move for source, target in pairs for move in _file_moves(source, target)]
    steps = _displaced(moves, time.strftime("%Y%m%d%H%M%S")) + moves
    done = []
    try:
        for source, target in steps:
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"Migration destination already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(source, target)
            done.append((source, target))
    except OSError:
        for source, target in reversed(done):
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(target, source)
        raise
    for source, _ in pairs:
        _prune_empty_dirs(source)
    (root / "src/settings.json.lock").unlink(missing_ok=True)
    return pairs


def refuse_unmigrated(root: Path) -> None:
    """Exit rather than run on an empty home beside unmigrated install state.

    A Flask-only restart after upgrading would otherwise open fresh stores
    (no chats, owner bootstrap reopened) while the real ones sit unmigrated.
    An explicit ``CUTTLE_HOME`` (tests, shadow instances) opts out.
    """
    if (os.environ.get("CUTTLE_HOME") or "").strip():
        return
    pending = pending_install_state(root)
    if pending:
        raise SystemExit(
            "Cuttle state is still inside the install tree ("
            + ", ".join(str(p) for p in pending)
            + f"). Exit Cuttle from the tray and relaunch: the daemon moves it to {cuttle_home()}."
        )


def active_processes(root: Path) -> list[int]:
    import psutil

    found = []
    checkout = root.resolve()
    markers = ("cuttle_daemon", "web_chat_api", "cuttle_shadow_app")
    # Read attributes explicitly: process_iter(attrs=...) suppresses AccessDenied
    # and returns None, which must never be mistaken for evidence of no host.
    for proc in psutil.process_iter():
        if proc.pid == os.getpid():
            continue
        try:
            args = proc.cmdline()
            command = " ".join(args)
            if not any(marker in command for marker in markers):
                continue
            if str(checkout) in command:
                found.append(proc.pid)
                continue
            cwd = proc.cwd()
            if not cwd or not Path(cwd).is_absolute():
                raise RuntimeError(f"Cannot determine host working directory (PID {proc.pid}); migration refused")
            if Path(cwd).resolve().is_relative_to(checkout):
                found.append(proc.pid)
        except psutil.NoSuchProcess:
            # A process which exited cannot keep using the old storage.
            continue
        except psutil.AccessDenied as exc:
            raise RuntimeError(f"Cannot inspect process {proc.pid}; migration refused") from exc
    return found


def migrate_when_stopped(root: Path) -> list[tuple[Path, Path]]:
    """Startup/CLI guard; excludes the current daemon before it starts services."""
    if not migration_pairs(root):
        return []
    active = active_processes(root)
    if active:
        raise RuntimeError(f"Cuttle is running (PIDs {active}); exit via the tray before migration")
    return migrate(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Move files; requires Cuttle stopped")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.apply:
        try:
            pairs = migrate_when_stopped(root)
        except (RuntimeError, OSError) as exc:
            parser.error(str(exc))
    else:
        pairs = migration_pairs(root)
    for source, target in pairs:
        print(f"{source} -> {target}")


if __name__ == "__main__":
    main()
