#!/usr/bin/env python3
"""git.push action — status (no push) or push after the user clicked the form.

Params: CUTTLE_PARAM_MODE (status|push), CUTTLE_PARAM_REMOTE, CUTTLE_PARAM_BRANCH,
CUTTLE_PARAM_PATH (optional project cwd). Never --force.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _env(*names: str) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _resolve_cwd() -> Path:
    raw = _env("CUTTLE_PARAM_PATH", "CUTTLE_PARAM_REPO_ROOT")
    if raw:
        p = Path(raw)
        try:
            from core.runtime_paths import rewrite_windows_lab_path

            mapped = (rewrite_windows_lab_path(raw) or "").strip()
            if mapped:
                p = Path(mapped)
        except Exception:
            pass
        return p.resolve()
    return REPO_ROOT.resolve()


def main() -> int:
    mode = (_env("CUTTLE_PARAM_MODE") or "push").strip().lower()
    if mode not in ("status", "push"):
        print("Unknown mode '" + mode + "'. Use: status, push")
        return 1
    cwd = _resolve_cwd()
    if not (cwd / ".git").exists() and not (cwd / ".git").is_file():
        # nested: walk up
        found = None
        for parent in [cwd, *cwd.parents]:
            if (parent / ".git").exists():
                found = parent
                break
        if found is None:
            print(f"Not a git repository: {cwd}")
            return 1
        cwd = found

    from scripts.utilities.git_pending_changes import (
        git_push_command,
        git_push_target,
        sanitize_git_output,
    )

    target = git_push_target(str(cwd))
    remote = _env("CUTTLE_PARAM_REMOTE") or (target.get("remote") or "origin")
    branch = _env("CUTTLE_PARAM_BRANCH") or (target.get("branch") or "")
    url = target.get("url") or ""
    print(f"{remote}/{branch or '?'} → {url or '(no remote URL)'}")
    if mode == "status":
        return 0

    cmd, env = git_push_command(remote, branch)
    joined = " ".join(cmd)
    if "--force" in cmd or " -f" in joined or joined.endswith(" -f"):
        print("Refusing force-push.")
        return 1
    result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)
    out = sanitize_git_output((result.stdout or "") + (result.stderr or ""))
    print(out.strip() or ("Pushed " + remote + " " + (branch or "HEAD")))
    return 0 if result.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
