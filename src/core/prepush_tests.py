"""Local test gate for pushes: catch what CI would fail before GitHub runs.

Two tiers, both run against the pushed commit in a throwaway worktree so other
chats' uncommitted edits cannot hide or cause a failure:

* **Branch push** (``fast``): ``src/tests/quality`` plus the test files that
  touch the changed sources, bounded so the push card stays usable.
* **Release tag** (``vX.Y.Z``): the push is refused unless that commit has a
  recorded ``release-check`` pass — the full suite, as CI runs it.

Both tiers turn Python's ``EncodingWarning`` into an error, so a text read or
write relying on the platform default encoding (cp1252 on Windows) fails on
Linux too. Recurring CI failure themes and the check that catches each:
``docs/architecture/ci-failure-patterns.md``.

CLI (repo root, project venv, ``PYTHONPATH=src``)::

    python -m core.prepush_tests release-check [REV]   # full suite, records a pass
    python -m core.prepush_tests fast [BASE] [REV]     # what a branch push runs
    python -m core.prepush_tests status [REV]          # is REV release-checked?
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
QUALITY_DIR = "src/tests/quality"
# Browser tests need Playwright and take minutes; the release check runs them.
FAST_EXCLUDE = ("src/tests/e2e/",)
FAST_MAX_FILES = 60
FAST_TIMEOUT = float(os.environ.get("CUTTLE_PREPUSH_FAST_TIMEOUT", "300"))
RELEASE_TIMEOUT = 3 * 3600
RELEASE_TAG = re.compile(r"^refs/tags/v\d+\.\d+\.\d+")
ENCODING_ARGS = ("-W", "error::EncodingWarning")
SOURCE_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".css", ".html")
MAX_REPORTED = 20


def _git(*args: str, cwd: Path = REPO, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def venv_python(repo: Path = REPO) -> Optional[str]:
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        cand = repo / rel
        if cand.is_file():
            return str(cand)
    return None


def commit_of(rev: str, repo: Path = REPO) -> Optional[str]:
    proc = _git("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", cwd=repo)
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and out else None


# --- release-check stamps -------------------------------------------------
# Kept in the git dir: per-clone, never tracked, gone with the clone.

def _stamp_dir(repo: Path = REPO) -> Path:
    proc = _git("rev-parse", "--git-common-dir", cwd=repo)
    common = Path((proc.stdout or ".git").strip())
    if not common.is_absolute():
        common = repo / common
    return common / "cuttle" / "release-check"


def read_stamp(sha: str, repo: Path = REPO) -> Optional[dict]:
    try:
        data = json.loads((_stamp_dir(repo) / f"{sha}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("result") == "passed" else None


def write_stamp(sha: str, summary: str, repo: Path = REPO) -> Path:
    path = _stamp_dir(repo) / f"{sha}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"commit": sha, "result": "passed", "summary": summary,
                                "checked_at": int(time.time())}), encoding="utf-8")
    return path


# --- test selection -------------------------------------------------------

def changed_files(base: Optional[str], head: str, repo: Path = REPO) -> List[str]:
    args = ["diff", "--name-only", "--diff-filter=ACMR", f"{base}..{head}"] if base else \
        ["show", "--name-only", "--format=", head]
    proc = _git(*args, cwd=repo)
    return [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]


def _tracked_tests(head: str, repo: Path) -> List[str]:
    proc = _git("ls-tree", "-r", "--name-only", head, "--", "src/tests", cwd=repo)
    return [p for p in (proc.stdout or "").splitlines()
            if re.search(r"(^|/)test_[^/]*\.py$", p) and not p.startswith(FAST_EXCLUDE)]


def _tests_mentioning(stems: Iterable[str], head: str, repo: Path) -> List[str]:
    stems = sorted({s for s in stems if len(s) >= 4})
    if not stems:
        return []
    args = ["grep", "-l", "-w", "-F"]
    for stem in stems:
        args += ["-e", stem]
    proc = _git(*args, head, "--", "src/tests", cwd=repo)
    prefix = f"{head}:"
    return [ln[len(prefix):] if ln.startswith(prefix) else ln
            for ln in (proc.stdout or "").splitlines()]


def select_fast_tests(files: Sequence[str], head: str, repo: Path = REPO) -> Tuple[List[str], str]:
    """Return (pytest paths, note). Always includes the quality suite."""
    tests = set(_tracked_tests(head, repo))
    picked = {f for f in files if f in tests}
    stems = [Path(f).stem for f in files
             if f.startswith("src/") and f.endswith(SOURCE_SUFFIXES) and f not in tests]
    related = {t for t in _tests_mentioning(stems, head, repo) if t in tests}
    note = ""
    if len(picked | related) > FAST_MAX_FILES:
        note = (f"{len(picked | related)} related test files is over the push budget; ran the "
                f"changed tests only. Run release-check for the rest.")
        related = set()
    chosen = sorted((picked | related) - {t for t in tests if t.startswith(QUALITY_DIR + "/")})
    return [QUALITY_DIR, *chosen], note


# --- running --------------------------------------------------------------

class Worktree:
    """Detached scratch checkout of one commit, removed on exit."""

    def __init__(self, sha: str, repo: Path = REPO):
        # A real directory outside the system temp dir, named like CI's
        # checkout: tests treat programs under the temp dir as fakes (vendor
        # CLI guard), refuse symlinked ancestors (a checkout's temp/ may be a
        # link) and rewrite paths ending in ``Cuttle/src``.
        from core.runtime_paths import runtime_cache_path

        scratch = runtime_cache_path("prepush")
        scratch.mkdir(parents=True, exist_ok=True)
        self.sha, self.repo = sha, repo
        self.path = Path(tempfile.mkdtemp(prefix=f"{sha[:12]}-", dir=scratch.resolve())) / "Cuttle"

    def __enter__(self) -> Path:
        proc = _git("worktree", "add", "--detach", str(self.path), self.sha, cwd=self.repo, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError("git worktree add failed: " + (proc.stderr or "").strip()[:300])
        return self.path

    def __exit__(self, *exc) -> None:
        _git("worktree", "remove", "--force", str(self.path), cwd=self.repo, timeout=120)
        shutil.rmtree(self.path.parent, ignore_errors=True)


def run_pytest(paths: Sequence[str], cwd: Path, python: str, timeout: float,
               extra: Sequence[str] = ()) -> Tuple[str, List[str], str]:
    """Return (status, failed node ids, summary). status: passed | failed | timeout | error."""
    env = dict(os.environ, PYTHONWARNDEFAULTENCODING="1", PYTHONUTF8="0")
    env.pop("PYTHONPATH", None)  # pytest.ini sets pythonpath = src for the worktree
    basetemp = Path(tempfile.mkdtemp(prefix="cuttle-prepush-pytest-"))
    cmd = [python, "-m", "pytest", *paths, "-q", "-p", "no:cacheprovider", "-rfE",
           f"--basetemp={basetemp}", *ENCODING_ARGS, *extra]
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "timeout", [], f"tests did not finish within {int(timeout)}s"
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)
    out = proc.stdout or ""
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", out, re.M)
    tail = [ln for ln in out.splitlines()
            if re.search(r"\b(passed|failed|errors?|no tests ran)\b.* in [\d.]+s", ln)]
    last = ((proc.stderr or out).strip().splitlines() or [""])[-1]
    summary = tail[-1].strip("= ") if tail else last[:300]
    if proc.returncode == 0 or (proc.returncode == 5 and not failed):
        return "passed", [], summary or "no tests ran"
    return ("failed" if failed or proc.returncode == 1 else "error"), failed, summary or f"exit {proc.returncode}"


def fast_check(base: Optional[str], head: str, repo: Path = REPO) -> dict:
    python = venv_python(repo)
    if not python:
        return {"status": "skipped", "summary": "no project venv; tests not run"}
    paths, note = select_fast_tests(changed_files(base, head, repo), head, repo)
    with Worktree(head, repo) as wt:
        status, failed, summary = run_pytest(paths, wt, python, FAST_TIMEOUT)
    return {"status": status, "failed": failed, "summary": summary, "note": note,
            "files": len(paths) - 1}


def release_check(rev: str = "HEAD", repo: Path = REPO) -> dict:
    sha = commit_of(rev, repo)
    python = venv_python(repo)
    if not sha or not python:
        return {"status": "error", "summary": "unknown revision" if not sha else "no project venv"}
    with Worktree(sha, repo) as wt:
        status, failed, summary = run_pytest(["src/tests"], wt, python, RELEASE_TIMEOUT)
    result = {"status": status, "commit": sha, "failed": failed, "summary": summary}
    if status == "passed":
        result["stamp"] = str(write_stamp(sha, summary, repo))
    return result


# --- hook entry -----------------------------------------------------------

def toplevel(cwd: Optional[Path] = None) -> Optional[Path]:
    proc = _git("rev-parse", "--show-toplevel", cwd=cwd or Path.cwd())
    out = (proc.stdout or "").strip()
    return Path(out) if proc.returncode == 0 and out else None


def gate(refs: Sequence[Tuple[str, str, str, Optional[str], List[str]]],
         repo: Optional[Path] = None) -> List[dict]:
    """Check pushed refs. refs: (remote_ref, local_sha, remote_sha, base, new_commits).

    Returns blocking findings (``core.git_push_diagnostics.finding`` shape).
    """
    from core.git_push_diagnostics import finding

    repo = repo or toplevel()
    if repo is None:
        return []
    if os.environ.get("CUTTLE_PREPUSH_TESTS", "1") == "0":
        return []
    if not ((repo / "pytest.ini").is_file() and (repo / QUALITY_DIR).is_dir()):
        return []  # not a Cuttle checkout (the hook's own tests push scratch repos)
    hits: List[dict] = []
    for remote_ref, local_sha, _remote_sha, base, commits in refs:
        if RELEASE_TAG.match(remote_ref):
            sha = commit_of(local_sha, repo) or local_sha
            if not read_stamp(sha, repo):
                hits.append(finding(
                    "tests", "release-check has not passed for this commit", sha,
                    remote_ref, hint="PYTHONPATH=src .venv/bin/python -m core.prepush_tests "
                                     f"release-check {sha[:12]}"))
            continue
        if not remote_ref.startswith("refs/heads/") or not commits:
            continue
        result = fast_check(base, local_sha, repo)
        if result["status"] in ("failed", "error"):
            failed = result.get("failed") or [result.get("summary", "")]
            for node in failed[:MAX_REPORTED]:
                hits.append(finding("tests", result.get("summary", "tests failed"), local_sha, node))
        elif result["status"] in ("timeout", "skipped"):
            print(f"pre-push hook warning: tests {result['status']}: {result['summary']}", file=sys.stderr)
        if result.get("note"):
            print("pre-push hook note: " + result["note"], file=sys.stderr)
    return hits


def _print(result: dict) -> int:
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") in ("passed", "skipped") else 1


def main(argv: Sequence[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    verb, args = (argv[0] if argv else ""), list(argv[1:])
    if verb == "release-check":
        return _print(release_check(args[0] if args else "HEAD"))
    if verb == "fast":
        head = commit_of(args[1] if len(args) > 1 else "HEAD") or "HEAD"
        base = args[0] if args else (commit_of("@{upstream}") or None)
        return _print(fast_check(base, head))
    if verb == "status":
        sha = commit_of(args[0] if args else "HEAD") or ""
        stamp = read_stamp(sha) if sha else None
        print(json.dumps({"commit": sha, "release_checked": bool(stamp), "stamp": stamp}, indent=2))
        return 0 if stamp else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
