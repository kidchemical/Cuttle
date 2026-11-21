"""Owner-requested, offline relocation of the complete Cuttle home.

Keep the launch path as a directory link. The small journal and lock next to
that path survive a drive change and the two renames needed for the first move.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from core.runtime_data import active_processes
from core.runtime_paths import cuttle_home, is_shadow_instance
from managers.settings_storage import atomic_write, read_json, settings_lock


def _journal(home: Path) -> Path:
    return home.with_name(f".{home.name}.location.json")


def _identity(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def _linked(path: Path) -> bool:
    return path.is_symlink() or getattr(os.path, "isjunction", lambda _: False)(path)


def _link(path: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(path))
    else:
        path.symlink_to(target, target_is_directory=True)


def _unlink_dir_link(path: Path) -> None:
    if os.name == "nt" and not path.is_symlink():
        path.rmdir()  # directory junction
    else:
        path.unlink()


def _unavailable(home, record):
    if record.get("phase") == "retiring":
        return "The previous folder will finish cleanup at the next cold start."
    if is_shadow_instance():
        return "Storage relocation is disabled in an isolated development instance."
    if _linked(home) and str(home.resolve()) != record.get("active"):
        return "The launcher's data folder is already redirected outside Settings."
    if os.name != "nt" and not shutil.which("rsync"):
        return "Install rsync on this host to move its data folder."
    return None


def location() -> dict:
    home = cuttle_home()
    journal = _journal(home)
    record = read_json(journal)
    reason = _unavailable(home, record)
    return dict(version=1, current=str(home.resolve()), pending=record.get("destination"),
                error=record.get("error"), available=not reason, reason=reason)


def _target(raw, home: Path, install: Path) -> Path:
    if not isinstance(raw, str) or not raw.strip() or not Path(raw.strip()).is_absolute():
        raise ValueError("Enter an absolute path on the host.")
    path = Path(raw.strip())
    if path.anchor.startswith(("//", "\\\\")):
        raise ValueError("Choose a folder on a local drive.")
    if os.path.lexists(path):
        raise ValueError("Choose a new folder name. The destination already exists.")
    if not path.parent.is_dir():
        raise ValueError("The destination's parent folder must already exist.")
    target = path.resolve()
    source = home.resolve(strict=True)
    if source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError("Choose a destination outside the current Cuttle data folder.")
    if target.is_relative_to(install.resolve()) or source.is_relative_to(install.resolve()):
        raise ValueError("Data must live outside the Cuttle installation.")
    if install.resolve().is_relative_to(source) or Path.home().resolve().is_relative_to(source):
        raise ValueError("Only a dedicated Cuttle data folder can be moved.")
    if not os.access(target.parent, os.W_OK):
        raise ValueError("Cuttle cannot write to that parent folder.")
    return target


def configure(body, install: Path | None = None) -> dict:
    home = cuttle_home()
    journal = _journal(home)
    with settings_lock(journal.with_suffix(".lock")):
        record = read_json(journal)
        if record.get("phase") in ("copying", "switching", "retiring"):
            raise ValueError("Storage migration is in progress.")
        active = {key: record[key] for key in ("active", "active_token") if key in record}
        if isinstance(body, dict) and set(body) == {"cancel"} and body["cancel"] is True:
            record = active
        else:
            if not isinstance(body, dict) or set(body) != {"destination"}:
                raise ValueError("Specify a destination or cancel the pending move.")
            reason = _unavailable(home, record)
            if reason:
                raise ValueError(reason)
            target = _target(body["destination"], home, install or Path(__file__).resolve().parents[2])
            if not (home / "db" / "cuttle_auth.db").is_file():
                raise ValueError("The source must be an initialized Cuttle data folder.")
            record = dict(**active, phase="scheduled",
                          destination=str(target), parent_id=_identity(target.parent),
                          source=str(home.resolve()), source_id=_identity(home),
                          source_parent_id=_identity(home.resolve().parent),
                          token=uuid.uuid4().hex)
        atomic_write(journal, {"version": 1, **record})
    return location()


def _run(args: list, success=(0,)) -> str:
    result = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if result.returncode not in success:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise OSError(detail[-1] if detail else f"{args[0]} failed ({result.returncode})")
    return result.stdout


def _verify_windows(source: Path, target: Path):
    # Robocopy's comparison uses timestamps and sizes. Check the actual bytes
    # before allowing the original to be retired.
    for folder, dirs, files in os.walk(source):
        other = target / Path(folder).relative_to(source)
        if set(os.listdir(other)) != set(dirs + files):
            raise OSError("The copied directory contents differ from the original.")
        for name in dirs + files:
            original, copied = Path(folder) / name, other / name
            if _linked(original):
                if os.readlink(original) != os.readlink(copied):
                    raise OSError(f"Link verification failed for {name}")
                if name in dirs:
                    dirs.remove(name)
            elif name in files:
                with original.open("rb") as a, copied.open("rb") as b:
                    if hashlib.file_digest(a, "sha256").digest() != hashlib.file_digest(b, "sha256").digest():
                        raise OSError(f"Copy verification failed for {name}")


def _check_open_files(source: Path):
    """External tools may still have a database or log open after tray Exit."""
    import psutil
    for proc in psutil.process_iter():
        try:
            for file in proc.open_files():
                if Path(file.path).is_relative_to(source):
                    raise ValueError(f"PID {proc.pid} still has {file.path} open. Close it and schedule the move again.")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def _copy_verified(source: Path, destination: Path):
    import psutil
    for part in psutil.disk_partitions(all=True):
        if Path(part.mountpoint).is_relative_to(source):
            raise ValueError("The data folder contains a mounted filesystem. Unmount it before moving.")
    if os.name == "nt":
        flags = ["/E", "/COPY:DATS", "/DCOPY:DAT", "/SL", "/SJ", "/R:0", "/W:0", "/NFL", "/NDL", "/NP"]
        _run(["robocopy", str(source), str(destination), *flags], success=(0, 1))
        _verify_windows(source, destination)
    else:
        # Private destination root throughout the copy, including hosts where
        # the original root inherits permissive permissions from its parent.
        # Copy contents below the root, retaining their exact modes/ACLs.
        # A NUL-delimited list of children leaves the new root private.
        entries = [os.fsencode(p.name) for p in source.iterdir()]
        args = ["rsync", "-aHAXSr", "--from0", "--files-from=-", "--", str(source) + "/", str(destination) + "/"]
        data = b"\0".join(entries) + b"\0"
        for flags in (["--fsync"], ["--dry-run", "--checksum", "--delete", "--itemize-changes"]):
            result = subprocess.run([args[0], *flags, *args[1:]], input=data, capture_output=True)
            if result.returncode:
                raise OSError(result.stderr.decode(errors="replace").strip() or "rsync failed")
            if "--dry-run" in flags and result.stdout:
                raise OSError("The source changed during copy or metadata could not be preserved.")
        if set(os.listdir(source)) != set(os.listdir(destination)):
            raise OSError("The source changed during copy.")


def _check_home(home, record):
    if record.get("active"):
        marker = home / ".cuttle-volume-id"
        if (str(home.resolve()) != record["active"] or not marker.is_file()
                or marker.read_text(encoding="ascii") != record["active_token"]):
            raise RuntimeError("The selected Cuttle data drive is unavailable. Mount it before starting.")


def check_reader():
    """A directly launched Flask must never initialize stores in a cutover gap."""
    home = cuttle_home()
    if not home.name:
        return
    record = read_json(_journal(home))
    if record.get("phase") in ("copying", "switching"):
        raise RuntimeError("Data migration is in progress. Start the daemon to finish it.")
    _check_home(home, record)


def _finish(home, journal, record):
    """Retire only the identified original tree. Preserve older absolute paths."""
    backup = home.with_name(f".{home.name}.old-{record['token']}")
    if os.path.lexists(backup):
        if _linked(backup):
            original = Path(record["source"])
            if not (_linked(original) and original.resolve() == home.resolve()):
                if _identity(original.parent) != record["source_parent_id"]:
                    raise OSError("Previous storage drive is unavailable.")
                if os.path.lexists(original):
                    if _linked(original) or _identity(original) != record["source_id"]:
                        raise OSError("Previous storage changed; refusing to remove it.")
                    shutil.rmtree(original)
                _link(original, home)
            _unlink_dir_link(backup)
        else:
            if _identity(backup) != record["source_id"]:
                raise OSError("Previous storage changed; refusing to remove it.")
            shutil.rmtree(backup)
    atomic_write(journal, dict(version=1, active=record["destination"], active_token=record["token"]))


def _retire(home, journal, record):
    record.update(phase="retiring", active=record["destination"], active_token=record["token"])
    atomic_write(journal, record)
    try:
        _finish(home, journal, record)
    except OSError as exc:
        # The verified new store is already operational. Retry only the old
        # tree's cleanup on the next cold start (e.g. a Windows sharing lock).
        record["error"] = f"Move completed. Previous folder cleanup needs another cold start: {exc}"
        atomic_write(journal, record)


def apply_at_startup(install: Path):
    """Run before daemon imports open any home files. Never called from Flask."""
    home = cuttle_home()
    if not home.name:
        return
    journal = _journal(home)
    if not journal.exists():
        return
    with settings_lock(journal.with_suffix(".lock")):
        record = read_json(journal)
        phase = record.get("phase")
        if not phase:
            _check_home(home, record)
            return
        if active_processes(install):
            raise RuntimeError("A Cuttle host is still running. Exit it before moving storage.")
        destination = Path(record["destination"])
        backup = home.with_name(f".{home.name}.old-{record['token']}")
        link = home.with_name(f".{home.name}.next-{record['token']}")
        if phase in ("switching", "retiring"):
            if _linked(home) and home.resolve() == destination and home.is_dir():
                if _identity(home) != record["destination_id"]:
                    raise RuntimeError("The migration destination changed; refusing cleanup.")
                _retire(home, journal, record)
                return
            if phase == "retiring":
                raise RuntimeError("Mount the selected Cuttle data drive before starting.")
            if not os.path.lexists(home) and os.path.lexists(backup):
                backup.rename(home)
        _check_home(home, record)
        if _identity(home) != record["source_id"]:
            raise RuntimeError("The source data folder changed; refusing migration.")
        try:
            if phase != "scheduled":
                raise ValueError(f"Move interrupted. The original is active. Inspect the partial copy at {destination}.")
            _target(str(destination), home, install)
            if _identity(destination.parent) != record["parent_id"]:
                raise ValueError("The destination drive or parent folder changed. Schedule the move again.")
            source = home.resolve(strict=True)
            # Probe link capability before copying, especially on Windows.
            _link(link, source if os.name == "nt" else destination)
            record["phase"] = "copying"
            atomic_write(journal, record)
            if active_processes(install):
                raise ValueError("Another Cuttle host started during migration. Close it and schedule the move again.")
            destination.mkdir(mode=0o700)
            record["destination_id"] = _identity(destination)
            if os.name != "nt" and destination.stat().st_mode & 0o077:
                raise ValueError("Choose a filesystem that supports private directory permissions.")
            _check_open_files(source)
            _copy_verified(source, destination)
            _check_open_files(source)
            marker = destination / ".cuttle-volume-id"
            if record.get("active_token"):
                marker.unlink()
            with marker.open("x", encoding="ascii") as stream:
                stream.write(record["token"])
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "nt":
                _unlink_dir_link(link)
                _link(link, destination)
            if (_identity(destination.parent) != record["parent_id"]
                    or _identity(destination) != record["destination_id"]
                    or _identity(home) != record["source_id"]):
                raise ValueError("Storage changed during the copy. Schedule the move again.")
            record["phase"] = "switching"
            atomic_write(journal, record)
            home.rename(backup)
            try:
                link.rename(home)
            except OSError:
                backup.rename(home)
                raise
        except (OSError, ValueError) as exc:
            # A failed copy never displaces a usable home. Preserve ambiguous
            # destinations after a crash; never delete a pre-existing folder.
            if not home.is_dir() or _identity(home) != record["source_id"]:
                raise
            if _linked(link):
                _unlink_dir_link(link)
            if phase == "scheduled" and record.get("destination_id") and destination.is_dir() and _identity(destination) == record["destination_id"]:
                shutil.rmtree(destination)
            atomic_write(journal, dict(version=1, active=record.get("active"), active_token=record.get("active_token"), error=str(exc)))
            return
        _retire(home, journal, record)
