"""Measured cost-per-task from public coding benchmarks (for ``/cost``).

Sources, in preference order:

* **CursorBench** and **DeepSWE** from Epoch AI's benchmark hub
  (``benchmark_data.zip`` — CSVs, CC-BY 4.0, credit Epoch AI).
* **SWE-bench Verified (bash-only)** from the official leaderboard JSON —
  every entry runs the same mini-SWE-agent scaffold, so costs compare.

Costs from different benchmarks are on different scales (task size, harness),
so a ``/cost`` table uses **one** benchmark: the one covering the most rows.

Cache: ``src/data/cache/task_benchmarks_cache.json`` (24h TTL).
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from core.runtime_paths import runtime_cache_path

EPOCH_ZIP_URL = "https://epoch.ai/data/benchmark_data.zip"
SWEBENCH_URL = (
    "https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/master/data/leaderboards.json"
)
_CACHE_VERSION = 1
_CACHE_TTL_SEC = 24 * 60 * 60
_FETCH_TIMEOUT_SEC = 30
_USER_AGENT = "Mozilla/5.0 (compatible; Cuttle-TaskBenchmarks/1.0)"

BENCHMARKS: Dict[str, Dict[str, str]] = {
    "cursorbench": {
        "label": "CursorBench",
        "credit": "Epoch AI",
        "url": "https://epoch.ai/benchmarks/cursorbench",
    },
    "deepswe": {
        "label": "DeepSWE",
        "credit": "Epoch AI",
        "url": "https://epoch.ai/benchmarks/deepswe",
    },
    "swebench": {
        "label": "SWE-bench Verified (bash-only)",
        "credit": "SWE-bench",
        "url": "https://www.swebench.com/",
    },
}
BENCHMARK_ORDER = ("cursorbench", "deepswe", "swebench")

_EPOCH_CSVS = {
    "cursorbench": {
        "file": "cursorbench_external.csv",
        "cost": "Cost per task",
        "score": "Score",
        "effort": "Reasoning level",
        "tokens": "Tokens per task",
    },
    "deepswe": {
        "file": "deepswe_external.csv",
        "cost": "Mean cost (USD)",
        "score": "Pass@1",
        "effort": "Reasoning effort",
        "tokens": "Mean output tokens",
    },
}

_EFFORT_ALIASES = {
    "extra high": "xhigh",
    "extra-high": "xhigh",
    "x-high": "xhigh",
    "xhigh": "xhigh",
    "max": "max",
    "maximum": "max",
    "high": "high",
    "medium": "medium",
    "med": "medium",
    "low": "low",
    "minimal": "minimal",
    "none": "none",
}
# Preferred effort when the caller has none (``medium`` is the common default).
_EFFORT_FALLBACK = ("medium", "high", "low", "", "xhigh", "minimal", "max", "none")

_lock = threading.Lock()
_refresh_thread: Optional[threading.Thread] = None
_mem: Dict[str, Any] = {}


def _cache_path() -> Path:
    return runtime_cache_path("task_benchmarks_cache.json", project_root=Path(__file__).resolve().parents[2])


def normalize_effort(value: Any) -> str:
    s = str(value or "").strip().lower().replace("_", " ")
    return _EFFORT_ALIASES.get(s, "")


def model_key(name: str) -> str:
    """Loose join key: ``GPT-5.6 Luna`` / ``gpt-5-6-luna`` / ``gpt-5.6-luna`` → ``gpt56luna``."""
    s = (name or "").strip().lower()
    s = s.rsplit("/", 1)[-1]
    if s.startswith("cursor-"):
        s = s[len("cursor-") :]
    s = re.sub(r"-20\d\d-?\d\d-?\d\d$", "", s)
    s = re.sub(r"-preview$", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _split_version(version: str, effort_col: str) -> tuple:
    """``grok-4.7_high`` → (``grok-4.7``, ``high``); effort column wins when set."""
    base, eff = version, ""
    if "_" in version:
        head, tail = version.rsplit("_", 1)
        if normalize_effort(tail):
            base, eff = head, normalize_effort(tail)
    return base, normalize_effort(effort_col) or eff


def _num(value: Any) -> Optional[float]:
    try:
        s = str(value).strip().replace("$", "").replace(",", "")
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def parse_epoch_zip(data: bytes) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        for bench, spec in _EPOCH_CSVS.items():
            if spec["file"] not in names:
                continue
            with z.open(spec["file"]) as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig")):
                    cost = _num(r.get(spec["cost"]))
                    version = str(r.get("Model version") or "").strip()
                    if cost is None or not version:
                        continue
                    base, eff = _split_version(version, r.get(spec["effort"]) or "")
                    score = _num(r.get(spec["score"]))
                    if score is not None and score <= 1.0:
                        score *= 100.0
                    out.append(
                        {
                            "bench": bench,
                            "model": base,
                            "key": model_key(base),
                            "effort": eff,
                            "cost": round(cost, 4),
                            "score": round(score, 1) if score is not None else None,
                            "tokens": _num(r.get(spec["tokens"])),
                            "date": str(r.get("Release date") or ""),
                        }
                    )
    return out


def parse_swebench(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    boards = data.get("leaderboards") if isinstance(data, dict) else None
    for board in boards or []:
        if str(board.get("name") or "") != "Verified":
            continue
        for r in board.get("results") or []:
            cost = _num(r.get("instance_cost"))
            if cost is None:
                continue
            model = ""
            for tag in r.get("tags") or []:
                if str(tag).startswith("Model:"):
                    model = str(tag).split(":", 1)[1].strip()
                    break
            model = model or str(r.get("model_display") or r.get("name") or "")
            if not model:
                continue
            out.append(
                {
                    "bench": "swebench",
                    "model": model,
                    "key": model_key(model),
                    "effort": normalize_effort(r.get("reasoning_effort")),
                    "cost": round(cost, 4),
                    "score": _num(r.get("resolved")),
                    "tokens": None,
                    "date": str(r.get("date") or ""),
                }
            )
    return out


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
        return resp.read()


def fetch_entries() -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    errors: List[str] = []
    try:
        entries += parse_epoch_zip(_fetch(EPOCH_ZIP_URL))
    except Exception as exc:
        errors.append(f"Epoch AI: {exc}")
    try:
        entries += parse_swebench(json.loads(_fetch(SWEBENCH_URL).decode("utf-8-sig")))
    except Exception as exc:
        errors.append(f"SWE-bench: {exc}")
    return {"entries": entries, "errors": errors}


def _read_cache() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or int(data.get("version") or 0) < _CACHE_VERSION:
        return None
    return data


def refresh(*, force: bool = True) -> Dict[str, Any]:
    """Re-download every source; keeps the old cache when all fetches fail."""
    got = fetch_entries()
    if not got["entries"]:
        raise RuntimeError("; ".join(got["errors"]) or "no benchmark rows")
    payload = {
        "version": _CACHE_VERSION,
        "fetched_at": time.time(),
        "entries": got["entries"],
        "errors": got["errors"],
    }
    path = _cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    with _lock:
        _mem.clear()
        _mem.update(payload)
    return payload


def _safe_refresh() -> None:
    try:
        refresh()
    except Exception as exc:
        print(f"[task_benchmarks] refresh failed: {exc}", flush=True)


def load_entries(*, refresh_if_stale: bool = True, force_refresh: bool = False) -> Dict[str, Any]:
    """``{entries, fetched_at}``; fetches synchronously only when nothing is cached."""
    global _refresh_thread
    if force_refresh:
        try:
            return refresh()
        except Exception as exc:
            print(f"[task_benchmarks] refresh failed: {exc}", flush=True)
    with _lock:
        data = dict(_mem) if _mem.get("entries") else None
    if data is None:
        data = _read_cache()
        if data:
            with _lock:
                _mem.clear()
                _mem.update(data)
    if not data:
        if not refresh_if_stale:
            return {"entries": [], "fetched_at": 0}
        try:
            return refresh()
        except Exception as exc:
            print(f"[task_benchmarks] fetch failed: {exc}", flush=True)
            return {"entries": [], "fetched_at": 0}
    stale = time.time() - float(data.get("fetched_at") or 0) > _CACHE_TTL_SEC
    if stale and refresh_if_stale:
        with _lock:
            if not (_refresh_thread and _refresh_thread.is_alive()):
                _refresh_thread = threading.Thread(
                    target=_safe_refresh, name="task-benchmarks-refresh", daemon=True
                )
                _refresh_thread.start()
    return data


def index_by_bench(entries: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    idx: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for e in entries:
        idx.setdefault(str(e.get("bench") or ""), {}).setdefault(str(e.get("key") or ""), []).append(e)
    return idx


def match(
    bench_idx: Dict[str, List[Dict[str, Any]]],
    candidates: Iterable[str],
    effort: str = "",
) -> Optional[Dict[str, Any]]:
    """Best entry for the first candidate id with data; ``effort`` preferred, then medium…"""
    want = normalize_effort(effort) if effort else ""
    for cand in candidates:
        rows = bench_idx.get(model_key(cand))
        if not rows:
            continue
        by_eff = {}
        for r in rows:
            prev = by_eff.get(r.get("effort") or "")
            if prev is None or str(r.get("date") or "") > str(prev.get("date") or ""):
                by_eff[r.get("effort") or ""] = r
        order = ([want] if want else []) + [e for e in _EFFORT_FALLBACK if e != want]
        for eff in order:
            if eff in by_eff:
                return by_eff[eff]
        return next(iter(by_eff.values()))
    return None
