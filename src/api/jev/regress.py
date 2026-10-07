"""Hourly log scan → pick pytest files → notify on breakage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from api.jev import thresholds as T
from api.jev.client import JevError, get_client, jev_available
from api.jev.types import choice, noul
from core.runtime_paths import output_dir, query_logs_dir

REPO_ROOT = Path(__file__).resolve().parents[3]
LOGS_DIR = query_logs_dir()
STATE_PATH = output_dir() / "jev" / "last_regress.json"

# Named suites Jev may pick. Paths are pytest node prefixes from repo root.
SUITES: Dict[str, Dict[str, Any]] = {
    "none": {
        "label": "No tests — noise or expected failures",
        "paths": [],
    },
    "router": {
        "label": "Agent router / rage / outcomes",
        "paths": [
            "src/tests/router/test_agent_router.py",
            "src/tests/router/test_agent_router_repeats.py",
            "src/tests/router/test_agent_router_frustration.py",
            "src/tests/router/test_agent_router_use_cases.py",
        ],
    },
    "compiler": {
        "label": "Context Compiler / Brain",
        "paths": [
            "src/tests/brain/test_context_compiler.py",
            "src/tests/brain/test_context_delta.py",
        ],
    },
    "dashboards": {
        "label": "Dashboards / Model Benchmarks",
        "paths": ["src/tests/dashboards/test_dashboards.py"],
    },
    "jev": {
        "label": "Jev judgment layer",
        "paths": ["src/tests/integrations/test_jev.py"],
    },
    "harness": {
        "label": "Agent harness kernel",
        "paths": ["src/tests/harness/test_agent_harness_smoke.py"],
    },
}


def _repo_root() -> Path:
    return REPO_ROOT


def collect_recent_query_logs(*, lookback_s: float, limit: int = 24) -> List[Dict[str, Any]]:
    if not LOGS_DIR.is_dir():
        return []
    cutoff = time.time() - max(60.0, float(lookback_s))
    files = sorted(
        LOGS_DIR.glob("query_data_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    for path in files[:80]:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            {
                "query_id": data.get("query_id"),
                "success": data.get("success"),
                "error_message": str(data.get("error_message") or "")[:240],
                "user_input": str(data.get("user_input") or "")[:200],
                "pipeline_name": data.get("pipeline_name") or "",
                "mtime": mtime,
            }
        )
        if len(out) >= limit:
            break
    return out


def collect_evidence(*, lookback_s: float) -> Dict[str, Any]:
    outcomes: List[Dict[str, Any]] = []
    try:
        from api.agent_router.outcomes import outcomes_since

        for row in outcomes_since(seconds=lookback_s)[-40:]:
            outcomes.append(
                {
                    "decision_id": row.get("decision_id"),
                    "target": f"{row.get('target_agent')}/{row.get('target_model')}",
                    "success": bool(row.get("success")),
                    "failure_kind": row.get("failure_kind"),
                    "feedback": row.get("user_feedback"),
                    "reason": str(row.get("reason") or "")[:160],
                }
            )
    except Exception:
        pass
    logs = collect_recent_query_logs(lookback_s=lookback_s)
    fail_logs = [x for x in logs if x.get("success") is False]
    fail_out = [
        x for x in outcomes
        if not x.get("success") and x.get("failure_kind") not in ("none", "cancelled", None)
    ]
    return {
        "lookback_s": lookback_s,
        "query_logs": logs,
        "query_failures": fail_logs,
        "outcomes": outcomes,
        "outcome_failures": fail_out,
        "failure_count": len(fail_logs) + len(fail_out),
    }


def decide_regress(
    evidence: Dict[str, Any],
    *,
    client=None,
) -> Dict[str, Any]:
    """Ask Jev whether to run tests and which suite. Fail-open: skip."""
    skip = {
        "run_tests": False,
        "suite": "none",
        "paths": [],
        "reason": "skipped",
        "regress_noul": 0.0,
        "confidence": 0.0,
    }
    if evidence.get("failure_count", 0) <= 0:
        skip["reason"] = "no failures in window"
        return skip
    if client is None and not jev_available():
        skip["reason"] = "jev unavailable"
        return skip

    criteria = {k: v["label"] for k, v in SUITES.items()}
    state = {
        "failure_count": evidence.get("failure_count"),
        "query_failures": (evidence.get("query_failures") or [])[:12],
        "outcome_failures": (evidence.get("outcome_failures") or [])[:12],
    }
    questions = {
        "regressed": noul(
            "Do these recent Cuttle logs look like a product regression "
            "(new breakage in router, compiler, dashboards, harness) rather than "
            "a single bad user prompt, quota, or cancelled turn?",
            true="Likely a code/config regression",
            false="Noise, user error, or expected transport failure",
        ),
        "suite": choice(
            "If tests should run, which suite is most relevant? Use none when no tests would help.",
            criteria,
        ),
    }
    try:
        c = client or get_client(timeout_s=10.0)
        result = c.system_one(state, questions)
    except (JevError, Exception) as e:
        skip["reason"] = str(e)[:200]
        return skip

    n = float(result.get("regressed").noul or 0.0)
    suite = (result.get("suite").choice or "none").strip().lower()
    if suite not in SUITES:
        suite = "none"
    conf = float(result.get("suite").confidence or 0.0)
    run = n >= T.REGRESS_NOUL and suite != "none" and conf >= T.SUITE_CONFIDENCE_FLOOR
    return {
        "run_tests": run,
        "suite": suite,
        "paths": list(SUITES.get(suite, {}).get("paths") or []),
        "reason": f"jev regress={n:.2f} suite={suite} conf={conf:.2f}",
        "regress_noul": n,
        "confidence": conf,
        "probabilities": dict(result.get("suite").probabilities),
        "answers": result.to_dict()["answers"],
    }


def run_pytest(paths: List[str], *, timeout_s: int = T.PYTEST_TIMEOUT_S) -> Dict[str, Any]:
    if not paths:
        return {"ran": False, "returncode": 0, "stdout": "", "stderr": ""}
    py = sys.executable
    cmd = [py, "-m", "pytest", "-q", "--tb=line", *paths]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=max(30, int(timeout_s)),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        return {
            "ran": True,
            "returncode": int(proc.returncode),
            "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-2000:],
            "cmd": cmd,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ran": True,
            "returncode": 124,
            "stdout": ((e.stdout or b"") if isinstance(e.stdout, bytes) else (e.stdout or ""))[-2000:],
            "stderr": f"pytest timed out after {timeout_s}s",
            "cmd": cmd,
        }


def notify_broke(summary: str) -> None:
    try:
        from api.ui_notify import notify_tray

        notify_tray(summary, variant="error")
    except Exception:
        pass


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    p = path or STATE_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def run_regress_once(
    *,
    lookback_s: Optional[int] = None,
    client=None,
    run_tests: bool = True,
    notify: bool = True,
) -> Dict[str, Any]:
    from api.jev.config import load_jev_config

    cfg = load_jev_config()
    lookback = int(lookback_s or cfg.regress.lookback_s)
    evidence = collect_evidence(lookback_s=lookback)
    decision = decide_regress(evidence, client=client)
    pytest_result: Dict[str, Any] = {"ran": False, "returncode": 0}
    if run_tests and decision.get("run_tests") and decision.get("paths"):
        pytest_result = run_pytest(list(decision["paths"]))
        if notify and int(pytest_result.get("returncode") or 0) != 0:
            suite = decision.get("suite")
            notify_broke(
                f"Cuttle regress tests failed ({suite}). "
                f"See python -m api.jev regress — pytest exit {pytest_result.get('returncode')}."
            )
    report = {
        "ts": time.time(),
        "evidence": {
            "failure_count": evidence.get("failure_count"),
            "query_failures": evidence.get("query_failures"),
            "outcome_failures": evidence.get("outcome_failures"),
        },
        "decision": decision,
        "pytest": {
            "ran": pytest_result.get("ran"),
            "returncode": pytest_result.get("returncode"),
            "stdout_tail": (pytest_result.get("stdout") or "")[-1200:],
            "stderr_tail": (pytest_result.get("stderr") or "")[-800:],
        },
    }
    try:
        save_state(report)
    except OSError:
        pass
    return report
