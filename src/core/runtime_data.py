"""Offline runtime-data migration: PYTHONPATH=src python -m core.runtime_data."""
from __future__ import annotations

import argparse
from pathlib import Path

from core.runtime_paths import runtime_data_dir, secrets_dir

SESSION_FILES = (
    "antigravity", "claude", "codex", "cursor", "hermes", "muse", "opencode",
)
CACHE_FILES = ("models_dev_pricing_cache.json", "opencode_models_cache.json",
               "task_benchmarks_cache.json")


def migration_pairs(root: Path) -> list[tuple[Path, Path]]:
    base = runtime_data_dir(root)
    pairs = [(base / "workspace" / f"{agent}_cli_session_map.json",
              base / "sessions" / f"{agent}_cli_session_map.json") for agent in SESSION_FILES]
    pairs += [(base / "workspace" / "harness_last_agent_map.json",
               base / "sessions" / "harness_last_agent_map.json"),
              (base / "workspace" / "context_inject_snapshots.json",
               base / "brain" / "context_inject_snapshots.json")]
    pairs += [(base / "workspace" / name, base / "cache" / name) for name in CACHE_FILES]
    pairs += [(base / "workspace" / name, base / name)
              for name in ("supervised_tasks", "edit_attribution")]
    pairs += [(base / "db" / "action_hmac_secret", secrets_dir(root) / "action_hmac_secret")]
    pairs += [(base / "agent_memory", base / "archive" / "agent_memory"),
              (base / "workspace" / "gemini_cli_session_map.json",
               base / "archive" / "gemini_cli_session_map.json")]
    legacy = base / "harness_agents"
    if legacy.is_dir():
        pairs += [(p, root / ".cuttle_global" / "personal" / "agents" / p.name)
                  for p in legacy.iterdir() if p.is_dir()]
    return [(source, target) for source, target in pairs if source.exists()]


def migrate(root: Path) -> list[tuple[Path, Path]]:
    """Caller must ensure all Cuttle processes using this checkout are stopped.

    Preflight every destination before moving anything. Never merge or overwrite.
    Moves preserve bytes and SQLite sidecars because their whole directory moves.
    """
    from managers.settings_storage import migrate_settings, migration_needed

    pairs = migration_pairs(root)
    conflicts = [str(target) for _, target in pairs if target.exists() or target.is_symlink()]
    if conflicts:
        raise FileExistsError("Migration destinations already exist: " + ", ".join(conflicts))
    if migration_needed(root):
        for name in ("machine_settings.json", "ui_state.json"):
            target = runtime_data_dir(root) / "config" / name
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"Settings migration destination already exists: {target}")
    moved = []
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moved.append((source, target))
        settings_changes = migrate_settings(root) if migration_needed(root) else []
    except (OSError, ValueError):
        # Keep legacy generation coherent if a later rename fails.
        for source, target in reversed(moved):
            target.rename(source)
        raise
    workspace = runtime_data_dir(root) / "workspace"
    if workspace.is_dir() and not any(workspace.iterdir()):
        workspace.rmdir()
    return pairs + settings_changes


def active_processes(root: Path) -> list[int]:
    import os
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
    from managers.settings_storage import migration_needed

    if not migration_pairs(root) and not migration_needed(root):
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
        from managers.settings_storage import migration_needed
        if migration_needed(root):
            pairs += [(root / "src/settings.json", runtime_data_dir(root) / "config" / name)
                      for name in ("machine_settings.json", "ui_state.json")]
    for source, target in pairs:
        print(f"{source.relative_to(root)} -> {target.relative_to(root)}")


if __name__ == "__main__":
    main()
