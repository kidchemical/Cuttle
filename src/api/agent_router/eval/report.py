"""Format and persist router evaluation reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Compact chat summaries stay under typical Discord / chat limits.
_CHAT_SOFT_LIMIT = 3500


def _result_glyph(result: str) -> str:
    return {
        "pass": "OK",
        "preference_warning": "WARN",
        "expectation_failure": "FAIL",
        "invalid": "INVALID",
        "fallback": "FALLBACK",
        "api_error": "API",
        "manual_review": "REVIEW",
    }.get(result or "", result or "?")


def format_single_eval(result: Dict[str, Any]) -> str:
    d = result.get("decision") or {}
    lines = [
        "**Router evaluate** (decision only — no agent executed)",
        "",
        f"- Prompt: {result.get('prompt', '')[:240]}",
        f"- Task: `{d.get('task_type')}` · difficulty `{d.get('difficulty')}`",
        f"- Target: `{d.get('target_agent')}` / `{d.get('target_model')}`",
        f"- Confidence: {d.get('confidence')}",
        f"- Reason: {d.get('reason')}",
        f"- Decision id: `{d.get('decision_id')}`",
        f"- Latency: {result.get('latency_ms')} ms",
    ]
    if result.get("used_fallback"):
        lines.append(
            f"- Deterministic fallback: **yes**"
            + (f" ({result.get('api_error')})" if result.get("api_error") else "")
        )
    rc = result.get("router_config") or {}
    lines.append(
        f"- Router: mode `{rc.get('mode')}` · model `{rc.get('api_model')}`"
    )
    return "\n".join(lines)


def format_batch_preview(
    suite_name: str,
    case_count: int,
    api_model: str,
    concurrency: int,
    repeats: int = 1,
) -> str:
    n_rep = max(1, int(repeats or 1))
    calls = case_count * n_rep
    lines = [
        f"**Router evaluate batch** — suite `{suite_name}`",
        "",
        f"- Cases: **{case_count}** × **{n_rep}** repeats = **{calls}** routing-brain calls",
        f"- Router model: `{api_model}`",
        f"- Concurrency: {concurrency}",
        f"- Executors: **none** (Cursor / Codex / Hermes will not run)",
        "",
        "This makes **paid OpenAI API calls** to the routing brain only.",
        f"To run: `/router evaluate batch {suite_name} --repeat {n_rep} --concurrency {concurrency} --yes`",
    ]
    if n_rep == 1:
        lines.append(
            f"Optional: `/router evaluate batch {suite_name} --repeat 5 --concurrency {concurrency} --yes`"
        )
    return "\n".join(lines)


def _conf_cell(case: Dict[str, Any]) -> str:
    stats = case.get("confidence_stats") or {}
    if stats.get("n"):
        return f"{stats.get('min'):.2f}/{stats.get('mean'):.2f}/{stats.get('max'):.2f}"
    conf = case.get("confidence")
    if isinstance(conf, (int, float)):
        return f"{conf:.2f}"
    return "-"


def _stability_cell(case: Dict[str, Any]) -> str:
    st = case.get("stability") or {}
    if not st:
        return "-"
    tgt = st.get("target") or {}
    if tgt.get("oscillates_auto_grok"):
        return "OSC Auto↔Grok"
    if not st.get("fully_stable"):
        parts = []
        if not (st.get("task_type") or {}).get("stable", True):
            parts.append("task")
        if not (st.get("difficulty") or {}).get("stable", True):
            parts.append("diff")
        if not tgt.get("stable", True):
            parts.append("tgt")
        return "unstable:" + ",".join(parts) if parts else "unstable"
    return "stable"


def format_batch_summary(
    report: Dict[str, Any],
    *,
    artifact_url: str = "",
    full: bool = False,
) -> str:
    c = report.get("counts") or {}
    router = report.get("router") or {}
    repeats = int(report.get("repeats") or 1)
    lines = [
        f"**Router evaluate batch** — suite `{report.get('suite')}` v{report.get('suite_version')}",
        "",
        f"- Router: `{router.get('mode')}` / `{router.get('api_model')}`",
        f"- Cases: {c.get('total', 0)}"
        + (f" · decisions: {report.get('decision_count', c.get('total', 0))}" if repeats > 1 else ""),
        f"- Repeats per case: {repeats}",
        f"- Passed: {c.get('passed', 0)}",
        f"- Preference warnings: {c.get('preference_warnings', 0)}",
        f"- Expectation failures: {c.get('expectation_failures', 0)}",
        f"- Invalid decisions: {c.get('invalid', 0)}",
        f"- Manual review: {c.get('manual_review', 0)}",
        f"- Fallback / API: {c.get('fallback', 0)} / {c.get('api_error', 0)}",
        f"- Elapsed: {report.get('elapsed_ms')} ms · concurrency {report.get('concurrency')}",
    ]
    ss = report.get("stability_summary") or {}
    if repeats > 1 and ss:
        lines.append(
            f"- Target oscillation (Auto↔Grok): {ss.get('oscillation_count', 0)}"
            + (
                f" — `{', '.join(ss.get('cases_with_target_oscillation') or [])}`"
                if ss.get("cases_with_target_oscillation")
                else ""
            )
        )
        lines.append(
            f"- Unstable target cases: {ss.get('unstable_target_count', 0)}"
            + (
                f" — `{', '.join(ss.get('cases_with_unstable_target') or [])}`"
                if ss.get("cases_with_unstable_target")
                else ""
            )
        )
    if artifact_url:
        lines.append(f"- Full report: {artifact_url}")

    if repeats > 1:
        lines.extend(
            [
                "",
                "| ID | Expected | Mode actual | Conf min/mean/max | Stab | Result |",
                "|---|---|---|---|---|---|",
            ]
        )
        for case in report.get("cases") or []:
            cid = str(case.get("id") or "")[:28]
            exp = str(case.get("expected_summary") or "")[:36].replace("|", "/")
            act = str(case.get("actual_summary") or "")[:24].replace("|", "/")
            conf_s = _conf_cell(case)
            stab = _stability_cell(case)
            res = _result_glyph(str(case.get("result") or ""))
            lines.append(f"| `{cid}` | {exp} | {act} | {conf_s} | {stab} | {res} |")
    else:
        lines.extend(["", "| ID | Expected | Actual | Conf | Result |", "|---|---|---|---:|---|"])
        for case in report.get("cases") or []:
            cid = str(case.get("id") or "")[:28]
            exp = str(case.get("expected_summary") or "")[:40].replace("|", "/")
            act = str(case.get("actual_summary") or "")[:28].replace("|", "/")
            conf_s = _conf_cell(case)
            res = _result_glyph(str(case.get("result") or ""))
            lines.append(f"| `{cid}` | {exp} | {act} | {conf_s} | {res} |")

    # Detail failures / warnings / oscillations (compact)
    interesting = [
        c
        for c in (report.get("cases") or [])
        if c.get("result")
        in ("expectation_failure", "invalid", "fallback", "api_error", "preference_warning")
        or (c.get("stability") or {}).get("target", {}).get("oscillates_auto_grok")
        or (
            repeats > 1
            and not (c.get("stability") or {}).get("fully_stable", True)
        )
    ]
    if interesting:
        lines.append("")
        lines.append("**Details**")
        for case in interesting[:20 if full else 12]:
            lines.append("")
            lines.append(f"- `{case.get('id')}` · {_result_glyph(str(case.get('result')))}")
            lines.append(f"  - Prompt: {(case.get('prompt') or '')[:160]}")
            lines.append(f"  - Expected: {case.get('expected_summary')}")
            lines.append(
                f"  - Actual (mode): {case.get('actual_summary')} "
                f"({case.get('task_type')}/{case.get('difficulty')}) "
                f"— {case.get('reason')}"
            )
            st = case.get("stability") or {}
            if st:
                lines.append(
                    f"  - Stability: task={st.get('task_type', {}).get('values')} · "
                    f"diff={st.get('difficulty', {}).get('values')} · "
                    f"target={st.get('target', {}).get('values')}"
                )
                if (st.get("target") or {}).get("oscillates_auto_grok"):
                    lines.append("  - **Flag: Auto ↔ Grok 4.6 oscillation**")
            cs = case.get("confidence_stats") or {}
            if cs.get("n"):
                lines.append(
                    f"  - Confidence min/mean/max: {cs.get('min')}/{cs.get('mean')}/{cs.get('max')}"
                )
            if case.get("api_error"):
                lines.append(f"  - Error: {case.get('api_error')}")
            if case.get("failure_reasons"):
                lines.append(f"  - Why: {'; '.join(case['failure_reasons'])}")
            if full and case.get("runs"):
                lines.append(f"  - Raw runs: {len(case['runs'])} (see JSON)")

    text = "\n".join(lines)
    if not full and len(text) > _CHAT_SOFT_LIMIT:
        text = text[: _CHAT_SOFT_LIMIT - 80].rstrip() + "\n\n…(truncated — see full report artifact)"
        if artifact_url:
            text += f"\n{artifact_url}"
    return text


def save_eval_report(report: Dict[str, Any], *, logs_dir: Optional[Path] = None) -> Tuple[Path, Path, str]:
    """Write JSON + markdown beside the query logs (served at ``/logs/``). Returns (json_path, md_path, url)."""
    if logs_dir is None:
        from core.runtime_paths import query_logs_dir

        logs_dir = query_logs_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suite = str(report.get("suite") or "suite")
    base = f"router_eval_{suite}_{ts}"
    payload = dict(report)
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    json_path = logs_dir / f"{base}.json"
    md_path = logs_dir / f"{base}.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(
        format_batch_summary(payload, artifact_url=f"/logs/{json_path.name}", full=True),
        encoding="utf-8",
    )
    return json_path, md_path, f"/logs/{json_path.name}"
