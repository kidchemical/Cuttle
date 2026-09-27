"""Pytest configuration and path setup for Cuttle tests.
Ensures src/ is on sys.path so imports like scripts.utilities.cursor_cli_tool work.
"""
import importlib
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import os
import traceback

import pytest

# Add src to path (conftest lives in tests/)
src_root = Path(__file__).resolve().parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))


def pytest_configure(config):
    """Fail-closed: unit tests must not launch real Cursor/Codex/API runners."""
    try:
        from api.agent_router.supervised.test_isolation import activate_test_isolation

        activate_test_isolation(reason="pytest_configure")
    except Exception as e:
        print(f"[conftest] supervised test isolation not activated: {e}", flush=True)
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


@pytest.fixture(autouse=True)
def _no_live_steer_servers(monkeypatch):
    """Adapters must not spawn real ``codex app-server`` / ``muse serve`` in tests."""
    monkeypatch.setenv("CUTTLE_AGENT_STEER", "0")


@pytest.fixture(autouse=True)
def _isolated_router_outcomes(tmp_path, monkeypatch):
    """Chat/router turns in tests must not land in the live My Cuttle Performance store."""
    monkeypatch.setenv("CUTTLE_ROUTER_DB", str(tmp_path / "router_outcomes.db"))
    monkeypatch.setenv("CUTTLE_JEV_LABEL_CACHE", str(tmp_path / "jev_turn_labels.json"))


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
