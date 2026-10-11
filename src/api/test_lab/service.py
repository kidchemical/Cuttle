"""Discover registered-project test suites and run them on explicit request."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from api.test_lab import store
from core.runtime_paths import venv_python
from managers.project_locations import ProjectUnavailable, require_project_path
from managers.project_manager import project_manager

RUN_TIMEOUT_SECONDS = 20 * 60
MAX_DISCOVERED_FILES = 20000
MAX_SUITES = 80
MAX_OUTPUT_CHARS = 24000
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", ".venv_wsl", "venv", "env",
    "node_modules", "bower_components", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".tox", ".nox", "dist", "build", "target",
    "coverage", "vendor", "temp", "tmp",
}
TEST_DIR_NAMES = {"tests", "test", "__tests__"}


class TestLabError(ValueError):
    pass


def _project(project_id: int) -> Tuple[Dict[str, Any], Path]:
    if project_manager is None:
        raise TestLabError("Project manager is unavailable.")
    project = project_manager.get_project(int(project_id))
    if not project or project.get("archived"):
        raise TestLabError("Project not found.")
    try:
        root = Path(require_project_path(project)).resolve(strict=True)
    except (ProjectUnavailable, OSError, RuntimeError) as exc:
        raise TestLabError(str(exc) or "Project path is unavailable.") from exc
    if not root.is_dir():
        raise TestLabError("Project path is not a directory.")
    return project, root


def list_projects() -> List[Dict[str, Any]]:
    if project_manager is None:
        raise TestLabError("Project manager is unavailable.")
    result = []
    for project in project_manager.get_projects():
        result.append({
            "id": int(project["id"]),
            "name": str(project.get("name") or "Project"),
            "available": bool(project.get("available")),
        })
    return result


def _walk(root: Path) -> Iterable[Tuple[Path, List[str], List[str]]]:
    root_depth = len(root.parts)
    seen = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        here = Path(current)
        depth = len(here.parts) - root_depth
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS
                   and not name.startswith(".") and depth < 12]
        # Do not scan arbitrary monorepos forever; registration can still show
        # every suite in the first 20k files, which is ample for an overview.
        remaining = MAX_DISCOVERED_FILES - seen
        if remaining <= 0:
            return
        seen += len(files)
        if len(files) > remaining:
            files = files[:remaining]
        yield here, dirs, files
        if seen >= MAX_DISCOVERED_FILES:
            return


def _is_python_test(name: str) -> bool:
    return name.startswith("test_") and name.endswith(".py") or name.endswith("_test.py")


def _relative(root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return rel or "."


def discover_suites(project_id: int) -> Dict[str, Any]:
    project, root = _project(project_id)
    py_files: List[Path] = []
    go_files: List[Path] = []
    js_test_files: List[Path] = []
    rust_files: List[Path] = []
    manifests: List[Path] = []

    # os.walk is intentionally bounded and skips generated/dependency trees.
    for directory, _dirs, files in _walk(root):
        for name in files:
            path = directory / name
            lower = name.lower()
            if _is_python_test(lower):
                py_files.append(path)
            if lower.endswith("_test.go"):
                go_files.append(path)
            if lower.endswith(".rs"):
                rust_files.append(path)
            if lower.endswith((".test.js", ".test.jsx", ".test.ts", ".test.tsx",
                               ".spec.js", ".spec.jsx", ".spec.ts", ".spec.tsx")):
                js_test_files.append(path)
            if lower in {"package.json", "cargo.toml", "go.mod"}:
                manifests.append(path)

    suites: List[Dict[str, Any]] = []
    py_dirs: Dict[str, List[Path]] = {}
    for path in py_files:
        parts = path.relative_to(root).parts
        parents = parts[:-1]
        test_dir = None
        for index, part in enumerate(parents):
            if part.lower() in TEST_DIR_NAMES:
                suite_path = Path(*parents[:index + 2]) if index + 1 < len(parents) else Path(*parents[:index + 1])
                test_dir = suite_path
                break
        if test_dir is None:
            test_dir = Path(*parents[:1]) if parents else Path(".")
        key = test_dir.as_posix() or "."
        py_dirs.setdefault(key, []).append(path)
    if py_dirs:
        for rel, files in sorted(py_dirs.items()):
            target = root / rel
            run_paths = ([_relative(root, path) for path in files]
                         if all(path.parent == target for path in files) else [rel])
            suites.append({
                "id": "pytest:" + rel,
                "label": "Python tests" if rel == "." else f"Python · {rel}",
                "framework": "pytest",
                "location": rel,
                "test_files": len(files),
                "_paths": run_paths,
                "_cwd": root,
            })

    for manifest in sorted(manifests):
        rel_dir = _relative(root, manifest.parent)
        if manifest.name.lower() == "package.json":
            try:
                package = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
            script = (package.get("scripts") or {}).get("test") if isinstance(package, dict) else None
            if isinstance(script, str) and script.strip():
                count = sum(1 for path in js_test_files if manifest.parent in path.parents)
                suites.append({
                    "id": "npm:" + rel_dir,
                    "label": "Node · npm test" if rel_dir == "." else f"Node · {rel_dir}",
                    "framework": "npm",
                    "location": rel_dir,
                    "test_files": count,
                    "_cwd": manifest.parent,
                })
        elif manifest.name.lower() == "cargo.toml":
            count = sum(
                1 for path in rust_files
                if manifest.parent in path.parents
                and ("test" in path.stem.lower()
                     or any(part.lower() in TEST_DIR_NAMES for part in path.relative_to(manifest.parent).parts[:-1]))
            )
            suites.append({
                "id": "cargo:" + rel_dir,
                "label": "Rust · cargo test" if rel_dir == "." else f"Rust · {rel_dir}",
                "framework": "cargo",
                "location": rel_dir,
                "test_files": count,
                "_cwd": manifest.parent,
            })
        elif manifest.name.lower() == "go.mod":
            count = sum(1 for path in go_files if manifest.parent in path.parents)
            suites.append({
                "id": "go:" + rel_dir,
                "label": "Go · go test ./..." if rel_dir == "." else f"Go · {rel_dir}",
                "framework": "go",
                "location": rel_dir,
                "test_files": count,
                "_cwd": manifest.parent,
            })

    # Avoid an incomplete inventory becoming a denial of service on huge repos.
    suites = suites[:MAX_SUITES]
    public_suites = [{key: value for key, value in suite.items() if not key.startswith("_")}
                     for suite in suites]
    return {
        "project": {"id": int(project["id"]), "name": str(project.get("name") or "Project")},
        "suites": public_suites,
        "suite_count": len(public_suites),
        "test_file_count": sum(int(suite.get("test_files") or 0) for suite in public_suites),
        "runs": store.recent_runs(project_id),
        "_root": root,
        "_suites": suites,
    }


def _command(suite: Dict[str, Any], root: Path) -> List[str]:
    framework = suite["framework"]
    if framework == "pytest":
        python = str(venv_python(root))
        return [python, "-m", "pytest", "-q", "--tb=short", *suite["_paths"]]
    if framework == "npm":
        if os.name == "nt":
            return ["cmd.exe", "/d", "/s", "/c", "npm test"]
        return ["npm", "test"]
    if framework == "cargo":
        return ["cargo", "test"]
    if framework == "go":
        return ["go", "test", "./..."]
    raise TestLabError("Unsupported test framework.")


def _safe_environment(root: Path) -> Dict[str, str]:
    allowed = {
        "PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT",
        "WINDIR", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["PATH"] = env.get("PATH") or os.defpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CI"] = "1"
    return env


def _run_suite(suite: Dict[str, Any], root: Path, timeout_s: int) -> Tuple[int, str]:
    cwd = Path(suite["_cwd"])
    command = _command(suite, root)
    display = " ".join(command)
    try:
        completed = subprocess.run(
            command, cwd=str(cwd), env=_safe_environment(root), shell=False,
            capture_output=True, text=True, timeout=max(1, int(timeout_s)),
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        seconds = max(1, int(timeout_s))
        limit = f"{seconds // 60} minutes" if seconds >= 60 else f"{seconds} seconds"
        return 124, f"$ {display}\nTest run timed out after {limit}.\n{out}"
    except OSError as exc:
        return 127, f"$ {display}\nCould not start test runner: {exc}"
    output = (completed.stdout or "")
    if completed.stderr:
        output += ("\n" if output else "") + completed.stderr
    return int(completed.returncode), f"$ {display}\n{output}"


def _execute(run_id: str, suites: List[Dict[str, Any]], root: Path) -> None:
    started = time.time()
    store.update_run(run_id, status="running")
    output_parts: List[str] = []
    final_code = 0
    status = "passed"
    try:
        for suite in suites:
            remaining = RUN_TIMEOUT_SECONDS - (time.time() - started)
            if remaining <= 0:
                final_code = 124
                status = "timed_out"
                output_parts.append("Test Lab stopped the suite list after the 20-minute run limit.")
                break
            code, output = _run_suite(suite, root, int(remaining))
            output_parts.append(output)
            if code != 0:
                final_code = code
                status = "timed_out" if code == 124 else "failed"
                if code == 124:
                    break
        output = "\n\n".join(output_parts)[-MAX_OUTPUT_CHARS:]
        store.update_run(
            run_id, status=status, finished_at=time.time(), duration_s=time.time() - started,
            exit_code=final_code, output=output,
        )
    except Exception as exc:
        store.update_run(
            run_id, status="failed", finished_at=time.time(), duration_s=time.time() - started,
            exit_code=1, output="\n\n".join(output_parts)[-MAX_OUTPUT_CHARS:], error=str(exc)[:1000],
        )


def start_run(project_id: int, suite_id: str) -> Dict[str, Any]:
    discovered = discover_suites(project_id)
    project = discovered["project"]
    root = discovered["_root"]
    all_suites = discovered["_suites"]
    if suite_id == "all":
        selected = all_suites
        label = "All test suites"
    else:
        selected = [suite for suite in all_suites if suite["id"] == suite_id]
        if not selected:
            raise TestLabError("That test suite is no longer available. Refresh the project list.")
        label = selected[0]["label"]
    if not selected:
        raise TestLabError("No supported test suites were found for this project.")
    frameworks = sorted({suite["framework"] for suite in selected})
    run_id = uuid.uuid4().hex
    run = {
        "id": run_id,
        "project_id": project["id"],
        "project_name": project["name"],
        "suite_id": suite_id,
        "suite_label": label,
        "framework": ", ".join(frameworks),
    }
    store.create_run(run)
    thread = threading.Thread(
        target=_execute,
        args=(run_id, selected, root),
        name=f"test-lab-{run_id[:8]}", daemon=True,
    )
    thread.start()
    return store.get_run(run_id) or run
