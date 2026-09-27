"""Task-aware verification modes for supervised review (no model calls).

Modes are intentionally small and typed — not a general verification language.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from api.agent_router.supervised.evidence import (
    EvidenceBundle,
    EvidenceItem,
    normalize_workspace_path,
)

# Finding classification labels for coordinator judgment.
FINDING_STATUS = frozenset(
    {
        "worker_claimed",
        "citation_located",
        "coordinator_verified",
        "not_verified",
        "contradicted",
    }
)

_MAX_SPOT_FILES = 8
_MAX_SPOT_BYTES = 48_000
_MAX_CONTEXT_CHARS = 2400


class VerificationMode(str, Enum):
    MUTATION = "mutation"
    READ_ONLY_CODE_REVIEW = "read_only_code_review"
    RESEARCH = "research"
    ARCHITECTURE = "architecture"
    GENERAL = "general"


# Explicit read-only / inspection language — takes precedence over mutation heuristics.
_READ_ONLY_PHRASES = (
    "read-only",
    "read only",
    "do not modify",
    "don't modify",
    "do not edit",
    "don't edit",
    "do not change files",
    "don't change files",
    "no code changes",
    "no file changes",
    "no modifications",
    "without changing",
    "without modifying",
    "do not modify files",
    "don't modify files",
)

_INSPECTION_PHRASES = (
    "code review",
    "static analysis",
    "inspect",
    "inspection",
    "identify",
    "documentation review",
    "docs review",
    "review the guide",
    "review this",
    "review the doc",
    "audit",
)

# Negated implement/edit phrases must not cancel read-only selection.
_NEGATED_MUTATION = re.compile(
    r"\b(?:do\s+not|don't|do\s+no|never|without)\s+"
    r"(?:implement|edit|modify|change|patch|mutate|write)\b",
    re.I,
)

# True mutation intent — word-boundary aware so "fixed"/"prefix"/"fixture" do not match.
_MUTATION_INTENT_RE = re.compile(
    r"(?:"
    r"\b(?:implement|mutate|refactor)\b|"
    r"\bapply\s+(?:the\s+)?(?:fix|patch)\b|"
    r"\bedit\s+(?:the\s+)?(?:files?|code|repo|repository)\b|"
    r"\b(?:write|change|modify)\s+(?:the\s+)?(?:files?|code)\b|"
    r"\bfix\s+(?:the\s+)?(?:bug|issue|error|regression)\b|"
    r"\badd\s+(?:a\s+)?(?:comment|feature|test|endpoint)\b"
    r")",
    re.I,
)


def _blob_has_explicit_mutation(blob: str) -> bool:
    cleaned = _NEGATED_MUTATION.sub(" ", blob or "")
    return bool(_MUTATION_INTENT_RE.search(cleaned))


def _blob_has_read_only_or_inspection(blob: str) -> bool:
    b = blob or ""
    if any(p in b for p in _READ_ONLY_PHRASES):
        return True
    if any(p in b for p in _INSPECTION_PHRASES):
        return True
    # Standalone "review" when paired with docs/guide/code/findings language.
    if re.search(r"\breview\b", b) and any(
        t in b
        for t in (
            "doc",
            "guide",
            "markdown",
            "code",
            "finding",
            "readme",
            "source",
            "file",
        )
    ):
        return True
    return False


def infer_verification_mode(
    *,
    user_objective: str = "",
    packet: Any = None,
) -> str:
    """Select a typed verification mode from intent/acceptance (heuristic, bounded).

    Precedence:
    1. Explicit read-only / inspect / review / identify → read_only_code_review
       (or architecture when clearly a design review).
    2. Explicit implementation / edit intent → mutation.
    3. Research / architecture signals.
    4. Ambiguous tasks without modification intent → general (not mutation).
    """
    bits: List[str] = [user_objective or ""]
    if packet is not None:
        bits.append(str(getattr(packet, "objective", "") or ""))
        bits.append(str(getattr(packet, "user_intent", "") or ""))
        for key in ("constraints", "non_goals", "acceptance_criteria", "verification"):
            vals = getattr(packet, key, None) or []
            if isinstance(vals, list):
                bits.extend(str(v) for v in vals)
        allowed = getattr(packet, "allowed_actions", None) or []
        if isinstance(allowed, list):
            bits.extend(str(a) for a in allowed)
    blob = " ".join(bits).lower()

    read_only_signal = _blob_has_read_only_or_inspection(blob)
    explicit_mutation = _blob_has_explicit_mutation(blob)

    allowed = list(getattr(packet, "allowed_actions", None) or []) if packet else []
    allowed_l = {str(a).lower() for a in allowed}
    allowed_mutation = bool(allowed_l & {"edit", "write", "implement", "patch", "mutate"})

    # 1) Explicit read-only / inspection wins unless the user clearly asked to edit.
    if read_only_signal and not (explicit_mutation or allowed_mutation):
        if "architecture" in blob or "design doc" in blob:
            return VerificationMode.ARCHITECTURE.value
        return VerificationMode.READ_ONLY_CODE_REVIEW.value

    # Read-only phrases still win over weak "fix"/"prefix" substring false positives
    # even when allowed_actions is empty but mutation regex misfired historically.
    if any(p in blob for p in _READ_ONLY_PHRASES) and not allowed_mutation:
        return VerificationMode.READ_ONLY_CODE_REVIEW.value

    if any(t in blob for t in ("research", "survey", "literature", "compare options")):
        return VerificationMode.RESEARCH.value
    if "architecture" in blob and not explicit_mutation:
        return VerificationMode.ARCHITECTURE.value

    if allowed_mutation or explicit_mutation:
        return VerificationMode.MUTATION.value

    return VerificationMode.GENERAL.value


def _finding_field(finding: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = finding.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def spot_check_finding(
    workspace: str,
    finding: Dict[str, Any],
    *,
    max_bytes: int = _MAX_CONTEXT_CHARS,
) -> Dict[str, Any]:
    """Read-only spot-check of a cited path/symbol. Never executes worker commands."""
    rel = _finding_field(finding, "file", "path", "filepath")
    symbol = _finding_field(finding, "symbol", "symbols", "function", "name")
    evidence = _finding_field(finding, "evidence", "quote", "snippet")
    if not rel:
        return {
            "status": "not_verified",
            "ok": None,
            "detail": "Finding missing repository-relative file path",
            "path": "",
            "excerpt": "",
        }
    path = normalize_workspace_path(workspace, rel)
    if path is None:
        # Also try stripping a leading src/ workspace mismatch.
        alt = rel
        if alt.startswith("src/"):
            path = normalize_workspace_path(workspace, alt[4:])
        if path is None and workspace.replace("\\", "/").rstrip("/").endswith("/src"):
            # Workspace is already src/ — allow paths rooted at repo.
            root_parent = str(Path(workspace).resolve().parent)
            path = normalize_workspace_path(root_parent, rel)
            if path is not None:
                workspace = root_parent
    if path is None:
        return {
            "status": "contradicted",
            "ok": False,
            "detail": f"Path rejected or outside workspace: {rel[:160]}",
            "path": rel,
            "excerpt": "",
        }
    if not path.is_file():
        return {
            "status": "contradicted",
            "ok": False,
            "detail": f"Cited file does not exist: {rel}",
            "path": rel,
            "excerpt": "",
        }
    try:
        data = path.read_bytes()
    except OSError as e:
        return {
            "status": "not_verified",
            "ok": None,
            "detail": f"Read failed: {e}",
            "path": rel,
            "excerpt": "",
        }
    if len(data) > 2_000_000:
        return {
            "status": "not_verified",
            "ok": None,
            "detail": "File too large for spot-check",
            "path": rel,
            "excerpt": "",
        }
    text = data.decode("utf-8", errors="replace")
    located = False
    excerpt = ""
    needle = symbol or (evidence[:80] if evidence else "")
    if needle:
        idx = text.find(needle)
        if idx < 0 and symbol:
            # Soft match: def/class symbol
            m = re.search(
                rf"(?m)^(?:async\s+)?(?:def|class)\s+{re.escape(symbol)}\b",
                text,
            )
            if m:
                idx = m.start()
                needle = m.group(0)
        if idx >= 0:
            located = True
            start = max(0, idx - 200)
            end = min(len(text), idx + max(len(needle), 1) + max_bytes)
            excerpt = text[start:end]
            if start > 0:
                excerpt = "…" + excerpt
            if end < len(text):
                excerpt = excerpt + "…"
    # Evidence paraphrase check (optional)
    evidence_hit = False
    if evidence:
        # Look for a distinctive token from evidence
        tokens = [t for t in re.findall(r"[A-Za-z_]{6,}", evidence) if t.lower() not in {
            "worker", "followup", "coordinate", "delivery", "session", "control"
        }]
        hits = sum(1 for t in tokens[:12] if t in text)
        evidence_hit = hits >= 2 or (needle and needle in text)

    if located and (not evidence or evidence_hit or not evidence):
        status = "citation_located"
        ok: Optional[bool] = True
        detail = f"Located `{symbol or needle}` in {rel}"
    elif located:
        status = "citation_located"
        ok = True
        detail = f"Symbol located in {rel}; evidence paraphrase weak"
    else:
        status = "not_verified"
        ok = None
        detail = f"Could not locate symbol/evidence in {rel}"
        excerpt = text[: min(len(text), 400)] + ("…" if len(text) > 400 else "")

    return {
        "status": status,
        "ok": ok,
        "detail": detail,
        "path": rel,
        "symbol": symbol,
        "excerpt": excerpt[: max_bytes + 200],
    }


def collect_mode_evidence(
    *,
    mode: str,
    workspace: str,
    worker_report: Any,
    base_bundle: Optional[EvidenceBundle] = None,
) -> EvidenceBundle:
    """Augment programmatic evidence according to verification mode."""
    bundle = base_bundle or EvidenceBundle(workspace=workspace or "")
    mode_s = (mode or VerificationMode.GENERAL.value).strip() or VerificationMode.GENERAL.value
    bundle.items.append(
        EvidenceItem(
            key="verification_mode",
            source="cuttle_verified",
            detail=f"mode={mode_s}",
            ok=True,
        )
    )

    findings = list(getattr(worker_report, "findings", None) or []) if worker_report else []
    no_mods = False
    for item in bundle.items:
        if item.key == "worktree_task_delta" and item.ok is True:
            no_mods = True
            break

    if mode_s == VerificationMode.READ_ONLY_CODE_REVIEW.value:
        if no_mods:
            bundle.items.append(
                EvidenceItem(
                    key="read_only_no_task_modifications",
                    source="cuttle_verified",
                    detail="Worktree delta shows no worker-caused modifications",
                    ok=True,
                )
            )
        total_bytes = 0
        for i, finding in enumerate(findings[:_MAX_SPOT_FILES]):
            if not isinstance(finding, dict):
                continue
            if total_bytes >= _MAX_SPOT_BYTES:
                bundle.items.append(
                    EvidenceItem(
                        key="spot_check_budget",
                        source="cuttle_verified",
                        detail="Spot-check byte budget exhausted; remaining findings not read",
                        ok=None,
                    )
                )
                break
            check = spot_check_finding(workspace, finding)
            total_bytes += len(check.get("excerpt") or "")
            fid = finding.get("id", i + 1)
            bundle.items.append(
                EvidenceItem(
                    key=f"finding_spot_check_{fid}",
                    source="cuttle_verified"
                    if check.get("status") == "citation_located"
                    else (
                        "cuttle_verified"
                        if check.get("status") == "contradicted"
                        else "not_verified"
                    ),
                    detail=(
                        f"status={check.get('status')} · {check.get('detail')} · "
                        f"path={check.get('path')}"
                    ),
                    ok=check.get("ok"),
                )
            )
            # Attach bounded excerpt as separate informational item
            if check.get("excerpt"):
                bundle.items.append(
                    EvidenceItem(
                        key=f"finding_excerpt_{fid}",
                        source="cuttle_verified",
                        detail=(check.get("excerpt") or "")[:800],
                        ok=check.get("ok"),
                    )
                )
        # Policy note for the coordinator — do not treat missing git proof as failure.
        bundle.items.append(
            EvidenceItem(
                key="verification_policy",
                source="cuttle_verified",
                detail=(
                    "read_only_code_review: do not escalate solely because git status "
                    "cannot prove a static-analysis claim. Classify findings as "
                    "worker_claimed|citation_located|coordinator_verified|not_verified|"
                    "contradicted using spot-checks."
                ),
                ok=True,
            )
        )
    elif mode_s == VerificationMode.MUTATION.value:
        bundle.items.append(
            EvidenceItem(
                key="verification_policy",
                source="cuttle_verified",
                detail=(
                    "mutation: prefer process exit, tests, worktree delta, changed-file "
                    "list, and explicit file checks."
                ),
                ok=True,
            )
        )
    elif mode_s in (
        VerificationMode.RESEARCH.value,
        VerificationMode.ARCHITECTURE.value,
    ):
        bundle.items.append(
            EvidenceItem(
                key="verification_policy",
                source="cuttle_verified",
                detail=(
                    f"{mode_s}: programmatic verification is limited; report uncertainty "
                    "rather than unsupported approval or automatic escalate."
                ),
                ok=None,
            )
        )
    else:
        bundle.items.append(
            EvidenceItem(
                key="verification_policy",
                source="cuttle_verified",
                detail="general: use available process/worktree evidence; treat gaps as uncertainty.",
                ok=None,
            )
        )

    return bundle


def reject_oversized_or_traversal_spot_request(
    workspace: str,
    relative_path: str,
    *,
    max_bytes: int = _MAX_SPOT_BYTES,
) -> Tuple[bool, str]:
    """Return (ok, reason) for a spot-check path request."""
    path = normalize_workspace_path(workspace, relative_path)
    if path is None:
        return False, "path_rejected_traversal_or_absolute"
    if not path.exists():
        return False, "path_not_found"
    try:
        size = path.stat().st_size
    except OSError as e:
        return False, f"stat_failed:{e}"
    if size > max_bytes * 40:  # hard cap on source files for spot-check
        return False, "file_too_large"
    return True, "ok"
