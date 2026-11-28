"""The worker's in-place code reload must reload dependencies before dependents.

Reloading only ``executor`` once left the new executor calling the old
``admission.requested`` after an update ("requested() takes 1 positional
argument but 2 were given" on the host's own worker).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from api.device_workers.capabilities import RELOAD_ORDER

PKG = Path(__file__).resolve().parents[2] / "api" / "device_workers"
_IMPORT = re.compile(r"^(\s*)from api\.device_workers(?:\.([\w.]+))? import ([\w, ]+)", re.M)


def _deps(path: Path, top_level_only: bool = False) -> set:
    """``api.device_workers`` modules a file imports (inside functions too, unless
    ``top_level_only``: a call-time import reads whatever is loaded when it runs)."""
    out = set()
    for indent, sub, names in _IMPORT.findall(path.read_text(encoding="utf-8")):
        if top_level_only and indent:
            continue
        if sub:
            out.add(f"api.device_workers.{sub}")
        else:
            out.update(f"api.device_workers.{n.strip()}" for n in names.split(","))
    return out


def test_every_reloaded_module_comes_after_what_it_imports_at_load():
    position = {name: i for i, name in enumerate(RELOAD_ORDER)}
    for name in RELOAD_ORDER:
        rel = name.removeprefix("api.device_workers.").replace(".", "/")
        path = PKG / (f"{rel}/__init__.py" if (PKG / rel).is_dir() else f"{rel}.py")
        for dep in _deps(path, top_level_only=True) - {name}:
            if dep in position:
                assert position[dep] < position[name], f"{name} imports {dep}, reloaded after it"


def test_execution_modules_reload_what_they_import():
    reloaded = set(RELOAD_ORDER)
    for name in ("executor", "adapters/comfyui"):
        missing = {d for d in _deps(PKG / f"{name}.py")
                   if d.rsplit(".", 1)[-1] not in {"store", "routes", "auth", "client", "worker_loop"}} - reloaded
        assert not missing, f"{name} imports modules the reload skips: {sorted(missing)}"


def test_a_reload_leaves_the_executor_calling_the_current_admission():
    code = (
        "from api.device_workers import executor, admission, capabilities\n"
        "from api.device_workers.adapters import comfyui\n"
        "admission.requested = lambda requirements: {}  # the pre-update code still in memory\n"
        "result = capabilities.reload_device_workers_modules()\n"
        "assert result['ok'], result\n"
        "from api.device_workers.admission import requested  # what execute_job imports at call time\n"
        "assert requested({'resources': {'ram_mb': 1}}, 'w') == {'ram_mb': 1}\n"
        "import api.device_workers.executor as ex, api.device_workers.adapters.comfyui as cf\n"
        "assert cf.JobExecError is ex.JobExecError\n"
        "print('ok')\n"
    )
    src = Path(__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-c", code], cwd=src, capture_output=True, text=True, timeout=120,
                          encoding="utf-8", errors="replace")
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-2000:]
