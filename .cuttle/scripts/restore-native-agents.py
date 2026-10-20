"""Host-side recovery from executable wrappers; no agent/model requests.

Run with --apply through the allowlisted action from a sandboxed chat. The
default is a read-only path report. Never stop/restart the hosting runtime.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from api.agent_harness import resource_scope
from api.agent_harness.cli_paths import CLI_PATH_ENV, describe_cli_paths, save_cli_paths
from managers.settings_manager import get_settings_manager


def verify_scope() -> dict:
    """Check actual kernel cgroup values and descendant inheritance, using Python only."""
    limits = resource_scope._load_limits()
    if limits is None:
        raise RuntimeError("Resource scopes are disabled by the host environment/settings.")
    code = (
        "import os,sys,json,subprocess; sys.stdin.readline(); "
        "child=subprocess.check_output([sys.executable,'-c',"
        "'import os,json; print(json.dumps(dict(uid=os.getuid(),cgroup=open(\"/proc/self/cgroup\").read())))'],text=True); "
        "print(json.dumps(dict(uid=os.getuid(),cgroup=open('/proc/self/cgroup').read(),child=json.loads(child))))"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not resource_scope.confine(proc):
            raise RuntimeError("Host systemd scope creation or aggregate limits failed; see action output.")
        cg = Path(f"/proc/{proc.pid}/cgroup").read_text().strip()
        expected = resource_scope.unit_name(resource_scope._comm(proc.pid), proc.pid)
        # The Python process retains its comm until exit.
        relative = next(line.split("::", 1)[1] for line in cg.splitlines() if line.startswith("0::"))
        if not relative.endswith("/" + expected) or resource_scope.SLICE not in relative.split("/"):
            raise RuntimeError(f"Unexpected cgroup: {cg}")
        scope = Path("/sys/fs/cgroup") / relative.lstrip("/")
        checked = {}
        for key, filename in (("memory_high", "memory.high"), ("memory_max", "memory.max"),
                              ("memory_swap_max", "memory.swap.max"), ("tasks_max", "pids.max")):
            if limits[key] is not None:
                actual = int((scope / filename).read_text().strip())
                if actual != limits[key]:
                    raise RuntimeError(f"{filename}: expected {limits[key]}, got {actual}")
                checked[filename] = actual
        if (scope / "memory.oom.group").read_text().strip() != "1":
            raise RuntimeError("Scope OOM group killing is not enabled.")
        for key, filename in (("slice_memory_high", "memory.high"), ("slice_memory_max", "memory.max")):
            if limits[key] is not None:
                # systemd/kernel round percentage limits down to a memory page.
                actual = int((scope.parent / filename).read_text().strip())
                if abs(actual - limits[key]) >= os.sysconf("SC_PAGE_SIZE"):
                    raise RuntimeError(f"Agent slice {filename} was not applied.")
                checked["slice_" + filename] = actual
        stdout, stderr = proc.communicate("verify\n", timeout=10)
        if proc.returncode:
            raise RuntimeError(f"Native child failed: {stderr[:300]}")
        identity = json.loads(stdout)
        if identity["uid"] != os.getuid() or identity["child"]["uid"] != os.getuid():
            raise RuntimeError("Native execution changed the user identity.")
        if identity["cgroup"].strip() != cg or identity["child"]["cgroup"].strip() != cg:
            raise RuntimeError("Descendant did not inherit the agent scope.")
        return {"uid": identity["uid"], "cgroup": relative, "limits": checked, "descendant_inherits": True}
    finally:
        if proc.poll() is None:
            proc.kill()  # Only this script's own idle smoke-test child.
        proc.communicate()


def apply() -> dict:
    if not sys.platform.startswith("linux"):
        raise RuntimeError("This recovery action is for Linux systemd hosts.")
    if os.environ.get("CUTTLE_BWRAP_ACTIVE") == "1":
        raise RuntimeError("Run this action on the host through Cuttle, outside the bwrap agent.")
    overrides = {key: os.environ[key] for key in CLI_PATH_ENV.values() if os.environ.get(key, "").strip()}
    if overrides:
        raise RuntimeError("Remove host CLI environment overrides first: " + ", ".join(overrides))
    save_cli_paths({agent: "" for agent in CLI_PATH_ENV})
    print("Saved CLI wrapper overrides cleared.", flush=True)
    if not get_settings_manager().set_setting(resource_scope.SETTING_KEY, dict(resource_scope.DEFAULTS)):
        raise RuntimeError("Could not enable resource limits.")
    verification = verify_scope()
    from scripts.utilities.claude_cli_tool import claude_executable
    from scripts.utilities.codex_cli_tool import codex_executable
    from scripts.utilities.cursor_cli_tool import _resolve_cursor_agent_argv
    from scripts.utilities.muse_cli_tool import _which_muse_native
    from api.agent_harness.agents.opencode.adapter import opencode_executable
    discovered = dict(claude=claude_executable(), codex=codex_executable(),
                      cursor=_resolve_cursor_agent_argv(), muse=_which_muse_native(), opencode=opencode_executable())
    for agent, value in discovered.items():
        paths = value if isinstance(value, list) else [value]
        for path in filter(None, paths):
            if any(marker in str(Path(path).resolve()) for marker in ("cuttle-agent-bwrap", "cuttle-agent-sandbox")):
                raise RuntimeError(f"{agent} still discovers a custom sandbox wrapper: {path}")
    return {"success": True, "cli_paths": describe_cli_paths(), "native_discovery": discovered,
            "systemd_verified": verification, "next": "Restart Flask using the restart card to load resource-scope code. Existing agent turns are unchanged."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(apply() if args.apply else describe_cli_paths(), indent=2))
    except Exception as exc:
        print(f"Recovery incomplete: {exc}", file=sys.stderr)
        sys.exit(1)
