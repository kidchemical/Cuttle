"""Pytest configuration and path setup for Cuttle tests.
Ensures src/ is on sys.path so imports like scripts.utilities.cursor_cli_tool work.
"""
import importlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import os
import tempfile
import traceback

import pytest

# Add src to path (conftest lives in tests/)
src_root = Path(__file__).resolve().parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

# Never collected, whichever pytest.ini / cwd is used (`--ignore` in addopts is
# cwd-relative, so `pytest src/tests/` from the repo root used to collect
# these). They drive the real mouse/keyboard/windows, launch apps, need live
# network/API keys, or import retired modules.
collect_ignore = [
    "e2e",
    # Paid provider diagnostics: run by hand with CUTTLE_ALLOW_SPEND=1.
    "diagnostics/test_api_key.py",
    "diagnostics/test_openai_connection.py",
]


def pytest_configure(config):
    """Fail-closed: unit tests must not launch real Cursor/Codex/API runners.

    Any isolation import/activation failure aborts the run instead of
    collecting tests unguarded.
    """
    # Hard offline override FIRST, before any other import below or any
    # collection-time import of api.mobile_android_update / web_chat_api:
    # register_mobile_android_update_routes() kicks a background Gradle
    # assembleDebug on import when the published APK is stale, and its
    # PYTEST_CURRENT_TEST guard is unset during collection. Test-only;
    # production default (auto-rebuild on) is unchanged.
    os.environ["CUTTLE_MOBILE_AUTO_REBUILD"] = "0"
    try:
        from api.agent_router.supervised.test_isolation import (
            activate_test_isolation,
            is_test_isolation_active,
        )
    except Exception as e:
        raise RuntimeError(
            "[conftest] supervised test isolation unavailable; "
            f"refusing to collect/run tests: {e}"
        )
    try:
        activate_test_isolation(reason="pytest_configure")
    except Exception as e:
        raise RuntimeError(
            "[conftest] supervised test isolation activation failed; "
            f"refusing to collect/run tests: {e}"
        )
    if not is_test_isolation_active():
        raise RuntimeError(
            "[conftest] supervised test isolation inactive after activation; "
            "refusing to collect/run tests"
        )
    _install_test_kill_guard()


def _install_test_kill_guard() -> None:
    """Block tests from signaling any PID that is not a child of this pytest."""
    if getattr(_install_test_kill_guard, "_installed", False):
        return
    pytest_pid = os.getpid()
    real_kill = os.kill
    real_killpg = getattr(os, "killpg", None)

    def _is_allowed_target(pid) -> bool:
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            return False
        if isinstance(pid, bool) or pid_i <= 1:
            return False
        if pid_i == pytest_pid:
            return True
        try:
            from api.process_kill_safety import is_descendant_of

            return is_descendant_of(pid_i, pytest_pid)
        except Exception:
            return False

    def _guarded_kill(pid, sig):
        if not _is_allowed_target(pid):
            test = os.environ.get("PYTEST_CURRENT_TEST") or "<collection>"
            stack = "".join(traceback.format_stack(limit=20))
            raise RuntimeError(
                f"TEST KILL GUARD: os.kill({pid!r}, {sig!r}) is not a pytest "
                f"descendant (pytest pid={pytest_pid}). test={test}\n{stack}"
            )
        return real_kill(pid, sig)

    os.kill = _guarded_kill  # type: ignore[assignment]
    if real_killpg is not None:
        def _guarded_killpg(pgid, sig):
            if pgid in (0, 1) or not _is_allowed_target(pgid):
                test = os.environ.get("PYTEST_CURRENT_TEST") or "<collection>"
                stack = "".join(traceback.format_stack(limit=20))
                raise RuntimeError(
                    f"TEST KILL GUARD: os.killpg({pgid!r}, {sig!r}) blocked. "
                    f"test={test}\n{stack}"
                )
            return real_killpg(pgid, sig)

        os.killpg = _guarded_killpg  # type: ignore[assignment]

    try:
        import psutil

        real_send = psutil.Process.send_signal
        real_term = psutil.Process.terminate
        real_kill_m = psutil.Process.kill

        def _guarded_send(self, sig):
            if not _is_allowed_target(self.pid):
                test = os.environ.get("PYTEST_CURRENT_TEST") or "<collection>"
                raise RuntimeError(
                    f"TEST KILL GUARD: Process.send_signal pid={self.pid} sig={sig} "
                    f"test={test}"
                )
            return real_send(self, sig)

        def _guarded_term(self):
            if not _is_allowed_target(self.pid):
                test = os.environ.get("PYTEST_CURRENT_TEST") or "<collection>"
                raise RuntimeError(
                    f"TEST KILL GUARD: Process.terminate pid={self.pid} test={test}"
                )
            return real_term(self)

        def _guarded_kill_m(self):
            if not _is_allowed_target(self.pid):
                test = os.environ.get("PYTEST_CURRENT_TEST") or "<collection>"
                raise RuntimeError(
                    f"TEST KILL GUARD: Process.kill pid={self.pid} test={test}"
                )
            return real_kill_m(self)

        psutil.Process.send_signal = _guarded_send  # type: ignore[assignment]
        psutil.Process.terminate = _guarded_term  # type: ignore[assignment]
        psutil.Process.kill = _guarded_kill_m  # type: ignore[assignment]
    except Exception:
        pass
    _install_test_kill_guard._installed = True  # type: ignore[attr-defined]


# Keep pytest from treating CUTTLE_TEST_ALLOW_EXTERNAL_RUNNERS as meaningful.
# Bypass is only via allow_external_runners() context manager.


@pytest.fixture(autouse=True)
def _isolated_application_settings(tmp_path, monkeypatch):
    """Tests get private settings; even cached production writers fail closed."""
    import managers.settings_manager as managers
    from managers.settings_storage import SettingsStorage

    manager = managers.SettingsManager(str(tmp_path / 'application-settings.json'))
    monkeypatch.setattr(managers, '_settings_manager', manager)
    production = (src_root / 'settings.json').resolve()
    update = SettingsStorage.update

    def private_update(self, key, transform):
        if self.server.resolve() == production:
            raise RuntimeError('Test attempted to write live application settings; use an isolated SettingsManager')
        return update(self, key, transform)

    monkeypatch.setattr(SettingsStorage, 'update', private_update)
    return manager


# --------------------------------------------------------------------------- #
# Resume-store discovery (shared by the resume contract + harness smoke tests)
# --------------------------------------------------------------------------- #

_SAVE_RE = re.compile(r"^save_(?P<agent>[a-z0-9_]+)_resume_id$")
_STORE_SKIP_PARTS = {"tests", "node_modules", "__pycache__", "dist", "win-unpacked", ".venv"}


def discover_resume_store_modules() -> List[Dict[str, Any]]:
    """Import every chat→CLI resume store under ``src/``.

    Discovered rather than hand-listed on purpose: these stores are copy-paste
    relatives (ERR-20260816-001 / -009 were the same bug in two of them), so a
    newly added agent's store must inherit the contract tests automatically.
    """
    found: List[Dict[str, Any]] = []
    for path in sorted(src_root.rglob("*session_store*.py")):
        rel = path.relative_to(src_root)
        if _STORE_SKIP_PARTS & set(rel.parts):
            continue
        module_name = ".".join(rel.with_suffix("").parts)
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - surfaced as a skip reason
            found.append({"name": module_name, "module": None, "import_error": exc})
            continue
        for attr in dir(module):
            match = _SAVE_RE.match(attr)
            if not match:
                continue
            agent = match.group("agent")
            loader = getattr(module, f"load_{agent}_resume_id", None)
            if loader is None:
                continue
            found.append(
                {
                    "name": agent,
                    "module": module,
                    "module_name": module_name,
                    "save": getattr(module, attr),
                    "load": loader,
                    "clear": getattr(module, f"clear_{agent}_resume_id", None),
                    "import_error": None,
                }
            )
    return found


def isolate_resume_store_files(stores, directory: Path, monkeypatch) -> None:
    """Point every store's map file at ``directory`` so tests never touch real state."""
    for store in stores:
        module = store.get("module")
        if module is None:
            continue
        assert hasattr(module, "_map_file"), (
            f"{store.get('module_name')} must expose _map_file() so tests can "
            "isolate it from the live session map"
        )
        target = directory / f"{store['name']}_session_map.json"
        monkeypatch.setattr(module, "_map_file", lambda target=target: target)


def pytest_generate_tests(metafunc):
    """Fan a test out over every discovered resume store when it asks for one."""
    if "resume_store" in metafunc.fixturenames:
        stores = discover_resume_store_modules()
        metafunc.parametrize(
            "resume_store", stores, ids=[str(s.get("name")) for s in stores]
        )


_VENDOR_AGENT_CLIS = {"agent", "cursor-agent", "codex", "muse", "hermes", "claude", "opencode"}


def _vendor_cli_in_argv(args) -> str:
    """Vendor CLI named by the program or its script, by file or folder name.

    Checks every path component of the first two argv entries: the Cursor CLI
    runs as ``~/.local/share/cursor-agent/versions/<v>/node index.js`` and npm
    CLIs as ``node …/codex/bin/codex.js``, so the binary name alone misses them.
    """
    argv = [args] if isinstance(args, (str, bytes, os.PathLike)) else list(args or [])
    argv = [os.fsdecode(a) for a in argv if isinstance(a, (str, bytes, os.PathLike))]
    if argv and Path(argv[0]).name.lower() in ("wsl", "wsl.exe"):
        argv = argv[1:]
    # Fake servers written to tmp_path (e.g. a `codex` shebang script) are fine.
    if argv and Path(argv[0]).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        return ""
    for entry in argv[:2]:
        for part in Path(entry).parts:
            name = part.lower()
            for ext in (".exe", ".cmd", ".bat", ".js", ".mjs"):
                if name.endswith(ext):
                    name = name[: -len(ext)]
                    break
            if name in _VENDOR_AGENT_CLIS:
                return name
    return ""


@pytest.fixture(autouse=True)
def _no_real_vendor_cli(monkeypatch):
    """Fail closed if a test would launch a real agent CLI (spends quota).

    Guards ``subprocess.Popen`` (asyncio subprocesses spawn through it too), so
    a stale seam — a fake patched on a function the route no longer calls —
    raises instead of silently running Cursor/Codex/Muse on the developer's
    login. Tests that fake ``create_subprocess_exec`` never reach Popen.
    """
    import subprocess

    from api.agent_router.supervised.test_isolation import external_runners_allowed

    real_popen = subprocess.Popen

    class _GuardedPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **k):
            cli = _vendor_cli_in_argv(args)
            if cli and not external_runners_allowed():
                raise RuntimeError(
                    f"test guard: real `{cli}` CLI launch blocked "
                    f"({os.environ.get('PYTEST_CURRENT_TEST', '?')}); fake the runner seam"
                )
            super().__init__(args, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", _GuardedPopen)


@pytest.fixture(autouse=True)
def _no_live_steer_servers(monkeypatch):
    """Adapters must not spawn real ``codex app-server`` / ``muse serve`` in tests."""
    monkeypatch.setenv("CUTTLE_AGENT_STEER", "0")


@pytest.fixture(autouse=True)
def _fresh_router_quota(monkeypatch):
    """Out-of-usage cooldowns and budget snapshots are process-global; one
    test's quota failure must not reroute every later routed turn."""
    from api.agent_router import budget, quota

    # Budget refreshes call vendor usage APIs with the developer's tokens.
    monkeypatch.setattr(budget, "_fetchers", lambda: {})
    quota.clear()
    budget.clear()
    yield
    quota.clear()
    budget.clear()


@pytest.fixture(autouse=True)
def _isolated_restart_files(tmp_path, monkeypatch):
    """Delivery end/cancel hooks must never consume a live pending restart.

    Tests have independent in-memory work registries; sharing the daemon's
    control files let an ordinary turn test declare the real Flask idle.
    """
    from api import flask_restart as restart

    monkeypatch.setattr(restart, 'STATUS_PATH', tmp_path / 'flask-restart-status.json')
    monkeypatch.setattr(restart, 'REQUEST_PATH', tmp_path / 'flask-restart-request.json')
    monkeypatch.setattr(restart, 'EVENTS_PATH', tmp_path / 'flask-restart-events.jsonl')


@pytest.fixture(autouse=True)
def _isolated_auth_db(tmp_path, monkeypatch):
    """Tests never read or write the live ``cuttle_auth.db``.

    ``get_auth_db`` is a lazy singleton, so pointing ``DB_PATH`` at a temp file
    and dropping the instance isolates every caller (including modules that
    from-imported ``get_auth_db``). Owner mode is single-user unless a test
    sets ``OWNER_USER_EMAIL`` itself — a developer's shell value must not turn
    freshly registered test users into non-owners. Empty (not unset) so the
    ``src/.env`` load on first ``web_chat_api`` import (override=False) cannot
    put it back mid-test.
    """
    import api.auth_db as auth_db

    monkeypatch.setattr(auth_db, "DB_PATH", tmp_path / "cuttle_auth.db")
    monkeypatch.setattr(auth_db, "_db_instance", None)
    monkeypatch.setenv("OWNER_USER_EMAIL", "")


class OwnerSession:
    def __init__(self, db, user_id, token):
        self.db = db
        self.user_id = user_id
        self.token = token

    def sign_in(self, client):
        client.set_cookie("session_token", self.token)
        return client


@pytest.fixture
def owner_session():
    """A real signed-in owner in the isolated auth DB (cookie via ``sign_in``)."""
    from api.auth_db import get_auth_db

    db = get_auth_db()
    user_id = db.create_user("owner@local", "Owner", "local", password="x")
    return OwnerSession(db, user_id, db.create_auth_session(user_id))


@pytest.fixture(autouse=True)
def _isolated_brain_state(tmp_path, monkeypatch):
    """Harness turns in tests must not write live Brain state or query logs.

    Briefing receipts, handoff cursors, context metrics, and query sidecars
    all default to ``src/data`` / ``src/web/logs``; pytest runs used to leave
    hundreds of ``muse-badge-session|…/pytest-of-…`` records there.
    """
    from api.cuttle_brain import context_delta, handoff
    from api.edit_attribution import journal

    monkeypatch.setattr(journal, "_db_path", lambda: tmp_path / "edit_journal.sqlite3")

    monkeypatch.setattr(context_delta, "_map_file", lambda: tmp_path / "brain_snapshots.json")
    monkeypatch.setattr(handoff, "_map_file", lambda: tmp_path / "brain_last_agent.json")
    monkeypatch.setenv("CUTTLE_CONTEXT_METRICS_DB", str(tmp_path / "context_metrics.db"))
    # Not created up front: the tracker makes it on first use.
    monkeypatch.setenv("CUTTLE_QUERY_LOG_DIR", str(tmp_path / "query_logs"))
    import api.query_tracker as qt

    monkeypatch.setattr(qt, "_fallback_tracker", None, raising=False)
    # Each test owns its trackers. Reordering subsystem suites must not leave a
    # previous test's unfinished query in the registry or thread-local lookup.
    import threading
    monkeypatch.setattr(qt, "_tls", threading.local())
    with qt._registry_lock:
        qt._active_trackers.clear()
    yield
    with qt._registry_lock:
        qt._active_trackers.clear()


@pytest.fixture(autouse=True)
def _isolated_router_outcomes(tmp_path, monkeypatch):
    """Chat/router turns in tests must not land in the live My Cuttle Performance store."""
    monkeypatch.setenv("CUTTLE_ROUTER_DB", str(tmp_path / "router_outcomes.db"))
    monkeypatch.setenv("CUTTLE_JEV_LABEL_CACHE", str(tmp_path / "jev_turn_labels.json"))


@pytest.fixture(autouse=True)
def _isolated_pairing_store(tmp_path, monkeypatch):
    """Chat admission tests must never use or write live approved identities."""
    from api import pairing_manager

    monkeypatch.setattr(pairing_manager, "PAIRING_STORE_FILE", tmp_path / "pairing.json")
    monkeypatch.setattr(pairing_manager, "_pairing_manager", None)


@pytest.fixture
def isolated_resume_stores(tmp_path, monkeypatch):
    """All discovered resume stores, redirected to a temp map file."""
    stores = [s for s in discover_resume_store_modules() if s.get("module") is not None]
    isolate_resume_store_files(stores, tmp_path, monkeypatch)
    return stores


_WINDOWS_ONLY_TEST_FILES = {
    "test_restart_daemon_script.py",
}


def pytest_collection_modifyitems(config, items):
    """Skip PowerShell / Win32-only tests on POSIX hosts."""
    if sys.platform == "win32":
        return
    skip_win = pytest.mark.skip(reason="Windows-only (PowerShell / Win32)")
    for item in items:
        try:
            name = Path(str(item.fspath)).name
        except Exception:
            continue
        if name in _WINDOWS_ONLY_TEST_FILES:
            item.add_marker(skip_win)
