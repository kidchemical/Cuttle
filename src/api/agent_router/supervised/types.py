"""Typed concepts for harness-neutral supervised coordination.

These are provider-independent. Adapters map them onto Codex/Cursor/etc.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class RoutingStrategy(str, Enum):
    """Router outcome kind — not an agent name."""

    DIRECT = "direct"
    SUPERVISED = "supervised"
    FRONTIER = "frontier"
    FALLBACK = "fallback"
    ASK_USER = "ask_user"


class SupervisedMode(str, Enum):
    OFF = "off"
    SUPERVISED = "supervised"


class TaskPhase(str, Enum):
    CREATED = "created"
    COORDINATING = "coordinating"
    DELEGATED = "delegated"
    WORKER_RUNNING = "worker_running"
    REVIEWING = "reviewing"
    FOLLOW_UP = "follow_up"
    AWAITING_USER = "awaiting_user"
    APPROVED = "approved"
    ESCALATED = "escalated"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ReviewAction(str, Enum):
    APPROVE = "approve"
    FOLLOW_UP = "follow_up"
    ESCALATE = "escalate"
    ASK_USER = "ask_user"
    FAIL = "fail"
    CANCEL = "cancel"


class EconomicSource(str, Enum):
    """Economic capacity source — separate from model identity."""

    CURSOR_AUTO_PROMO = "cursor_auto_promo"
    CURSOR_INCLUDED_QUOTA = "cursor_included_quota"
    CODEX_CHATGPT_ALLOCATION = "codex_chatgpt_allocation"
    OPENAI_API = "openai_api"
    LOCAL_COMPUTE = "local_compute"
    UNKNOWN = "unknown"
    PAID_REQUIRES_APPROVAL = "paid_requires_approval"


VALID_ROUTING_STRATEGIES = frozenset(s.value for s in RoutingStrategy)
VALID_SUPERVISED_MODES = frozenset(m.value for m in SupervisedMode)
VALID_REVIEW_ACTIONS = frozenset(a.value for a in ReviewAction)
# Normalized Cuttle reasoning levels (adapters map to harness flags).
VALID_REASONING_LEVELS = frozenset({"minimal", "low", "medium", "high", "xhigh"})
REASONING_ALIASES = {
    "light": "low",
    "lite": "low",
    "min": "minimal",
    "max": "xhigh",
}


def new_id(prefix: str = "") -> str:
    body = uuid.uuid4().hex[:12]
    return f"{prefix}{body}" if prefix else body


def normalize_reasoning_level(raw: Optional[str], *, default: str = "low") -> str:
    s = (raw or "").strip().lower()
    s = REASONING_ALIASES.get(s, s)
    if s in VALID_REASONING_LEVELS:
        return s
    return default


@dataclass(frozen=True)
class HarnessRef:
    """Replaceable harness identity (agent CLI or API)."""

    kind: str  # "agent" | "api" | "local"
    agent: str = ""
    model: str = ""
    reasoning: str = ""  # normalized Cuttle level; adapter translates
    economic_source: str = EconomicSource.UNKNOWN.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "agent": self.agent,
            "model": self.model,
            "reasoning": self.reasoning,
            "economic_source": self.economic_source,
        }

    @staticmethod
    def from_dict(raw: Any) -> Optional["HarnessRef"]:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "agent").strip().lower() or "agent"
        return HarnessRef(
            kind=kind,
            agent=str(raw.get("agent") or "").strip().lower(),
            model=str(raw.get("model") or "").strip(),
            reasoning=normalize_reasoning_level(raw.get("reasoning"), default=""),
            economic_source=str(raw.get("economic_source") or EconomicSource.UNKNOWN.value),
        )


@dataclass
class CoordinatorProfile:
    id: str
    label: str
    harness: HarnessRef
    may_edit_repo: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "harness": self.harness.to_dict(),
            "may_edit_repo": self.may_edit_repo,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(raw: Any) -> Optional["CoordinatorProfile"]:
        if not isinstance(raw, dict):
            return None
        h = HarnessRef.from_dict(raw.get("harness") or {})
        if not h:
            return None
        cid = str(raw.get("id") or "").strip()
        if not cid:
            return None
        return CoordinatorProfile(
            id=cid,
            label=str(raw.get("label") or cid),
            harness=h,
            may_edit_repo=bool(raw.get("may_edit_repo")),
            notes=str(raw.get("notes") or ""),
        )


@dataclass
class WorkerProfile:
    id: str
    label: str
    harness: HarnessRef
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "harness": self.harness.to_dict(),
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(raw: Any) -> Optional["WorkerProfile"]:
        if not isinstance(raw, dict):
            return None
        h = HarnessRef.from_dict(raw.get("harness") or {})
        if not h:
            return None
        wid = str(raw.get("id") or "").strip()
        if not wid:
            return None
        return WorkerProfile(
            id=wid,
            label=str(raw.get("label") or wid),
            harness=h,
            notes=str(raw.get("notes") or ""),
        )


@dataclass
class CoordinationBudget:
    max_followups: int = 1
    max_worker_attempts: int = 3
    max_coordinator_invocations: int = 8
    allow_paid_tier: bool = False
    worker_timeout_sec: float = 1800.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(raw: Any) -> "CoordinationBudget":
        if not isinstance(raw, dict):
            return CoordinationBudget()
        return CoordinationBudget(
            max_followups=max(0, min(5, int(raw.get("max_followups", 1) or 0))),
            max_worker_attempts=max(1, min(10, int(raw.get("max_worker_attempts", 3) or 3))),
            max_coordinator_invocations=max(
                1, min(20, int(raw.get("max_coordinator_invocations", 8) or 8))
            ),
            allow_paid_tier=bool(raw.get("allow_paid_tier")),
            worker_timeout_sec=float(raw.get("worker_timeout_sec") or 1800.0),
        )


@dataclass
class DelegationPacket:
    objective: str
    user_intent: str = ""
    workspace: str = ""
    constraints: List[str] = field(default_factory=list)
    non_goals: List[str] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    verification: List[str] = field(default_factory=list)
    relevant_files: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=list)
    permit_restarts: bool = False
    permit_paid_calls: bool = False
    permit_destructive: bool = False
    permit_external_effects: bool = False
    expected_report_format: str = "structured_json"
    raw: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "objective": self.objective,
            "user_intent": self.user_intent,
            "workspace": self.workspace,
            "constraints": list(self.constraints),
            "non_goals": list(self.non_goals),
            "acceptance_criteria": list(self.acceptance_criteria),
            "verification": list(self.verification),
            "relevant_files": list(self.relevant_files),
            "allowed_actions": list(self.allowed_actions),
            "permit_restarts": self.permit_restarts,
            "permit_paid_calls": self.permit_paid_calls,
            "permit_destructive": self.permit_destructive,
            "permit_external_effects": self.permit_external_effects,
            "expected_report_format": self.expected_report_format,
        }
        if self.raw:
            d["raw"] = self.raw
        return d

    @staticmethod
    def from_dict(raw: Any) -> Optional["DelegationPacket"]:
        if not isinstance(raw, dict):
            return None
        objective = str(raw.get("objective") or "").strip()
        if not objective:
            return None

        def _list(key: str) -> List[str]:
            v = raw.get(key)
            if isinstance(v, list):
                return [str(x).strip() for x in v if str(x).strip()]
            if isinstance(v, str) and v.strip():
                return [v.strip()]
            return []

        return DelegationPacket(
            objective=objective,
            user_intent=str(raw.get("user_intent") or raw.get("intent") or "").strip(),
            workspace=str(raw.get("workspace") or "").strip(),
            constraints=_list("constraints"),
            non_goals=_list("non_goals"),
            acceptance_criteria=_list("acceptance_criteria"),
            verification=_list("verification") or _list("verification_requirements"),
            relevant_files=_list("relevant_files"),
            allowed_actions=_list("allowed_actions"),
            permit_restarts=bool(raw.get("permit_restarts")),
            permit_paid_calls=bool(raw.get("permit_paid_calls")),
            permit_destructive=bool(raw.get("permit_destructive")),
            permit_external_effects=bool(raw.get("permit_external_effects")),
            expected_report_format=str(raw.get("expected_report_format") or "structured_json"),
            # Shallow snapshot only — never nest prior `raw` (save/load recursion).
            raw={k: v for k, v in dict(raw).items() if k != "raw"},
        )

    def to_worker_prompt(self) -> str:
        """Bounded prompt for the worker — not the full coordinator transcript."""
        lines = [
            "# Supervised task packet",
            "",
            "## Objective",
            self.objective,
        ]
        if self.user_intent:
            lines.extend(["", "## User intent", self.user_intent])
        if self.workspace:
            lines.extend(["", f"## Workspace", self.workspace])
        if self.constraints:
            lines.extend(["", "## Constraints", *[f"- {c}" for c in self.constraints]])
        if self.non_goals:
            lines.extend(["", "## Non-goals", *[f"- {c}" for c in self.non_goals]])
        if self.acceptance_criteria:
            lines.extend(
                ["", "## Acceptance criteria", *[f"- {c}" for c in self.acceptance_criteria]]
            )
        if self.verification:
            lines.extend(["", "## Verification", *[f"- {c}" for c in self.verification]])
        if self.relevant_files:
            lines.extend(["", "## Relevant files", *[f"- {c}" for c in self.relevant_files]])
        if self.allowed_actions:
            lines.extend(["", "## Allowed actions", *[f"- {c}" for c in self.allowed_actions]])
        lines.extend(
            [
                "",
                "## Permissions",
                f"- Restarts: {'yes' if self.permit_restarts else 'no'}",
                f"- Paid calls: {'yes' if self.permit_paid_calls else 'no'}",
                f"- Destructive actions: {'yes' if self.permit_destructive else 'no'}",
                f"- External effects: {'yes' if self.permit_external_effects else 'no'}",
                "",
                "## Required report",
                "When finished, end with a fenced JSON block:",
                "```json",
                "{",
                '  "outcome": "success|partial|failed|blocked",',
                '  "summary": "...",',
                '  "files_changed": [],',
                '  "tests_run": [],',
                '  "test_results": "...",',
                '  "limitations": [],',
                '  "questions": [],',
                '  "diff_stat": "...",',
                '  "acceptance_satisfied": true,',
                '  "external_effects": []',
                "}",
                "```",
            ]
        )
        return "\n".join(lines)


def _normalize_findings(raw: Any) -> List[Dict[str, Any]]:
    """Normalize worker findings to a list of plain dicts (untrusted evidence)."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(dict(item))
        elif item is not None and str(item).strip():
            out.append({"summary": str(item).strip()})
    return out


@dataclass
class WorkerReport:
    outcome: str = "unknown"
    summary: str = ""
    files_changed: List[str] = field(default_factory=list)
    tests_run: List[str] = field(default_factory=list)
    test_results: str = ""
    limitations: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    diff_stat: str = ""
    acceptance_satisfied: Optional[bool] = None
    external_effects: List[str] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    parse_ok: bool = False
    raw_output: str = ""
    parse_error: str = ""
    raw_output_chars: int = 0
    raw_output_bytes: int = 0
    summary_truncated: bool = False
    raw_artifact_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        raw = self.raw_output or ""
        return {
            "outcome": self.outcome,
            "summary": self.summary,
            "files_changed": list(self.files_changed),
            "tests_run": list(self.tests_run),
            "test_results": self.test_results,
            "limitations": list(self.limitations),
            "questions": list(self.questions),
            "diff_stat": self.diff_stat,
            "acceptance_satisfied": self.acceptance_satisfied,
            "external_effects": list(self.external_effects),
            "findings": [dict(f) for f in self.findings],
            "usage": dict(self.usage),
            "parse_ok": self.parse_ok,
            "parse_error": self.parse_error,
            "raw_output_len": len(raw),
            "raw_output_chars": self.raw_output_chars or len(raw),
            "raw_output_bytes": self.raw_output_bytes
            or len(raw.encode("utf-8", errors="replace")),
            "summary_truncated": self.summary_truncated,
            "raw_artifact_path": self.raw_artifact_path,
            # Full raw retained independently of chat preview truncation.
            "raw_output": raw,
        }

    @staticmethod
    def from_dict(raw: Any) -> "WorkerReport":
        if not isinstance(raw, dict):
            return WorkerReport()

        def _list(key: str) -> List[str]:
            v = raw.get(key)
            if isinstance(v, list):
                return [str(x) for x in v if str(x).strip()]
            return []

        acc = raw.get("acceptance_satisfied")
        text = str(raw.get("raw_output") or "")
        return WorkerReport(
            outcome=str(raw.get("outcome") or "unknown"),
            summary=str(raw.get("summary") or ""),
            files_changed=_list("files_changed"),
            tests_run=_list("tests_run"),
            test_results=str(raw.get("test_results") or ""),
            limitations=_list("limitations"),
            questions=_list("questions"),
            diff_stat=str(raw.get("diff_stat") or ""),
            acceptance_satisfied=bool(acc) if acc is not None else None,
            external_effects=_list("external_effects"),
            findings=_normalize_findings(raw.get("findings")),
            usage=dict(raw.get("usage") or {}) if isinstance(raw.get("usage"), dict) else {},
            parse_ok=bool(raw.get("parse_ok")),
            raw_output=text,
            parse_error=str(raw.get("parse_error") or ""),
            raw_output_chars=int(raw.get("raw_output_chars") or len(text) or 0),
            raw_output_bytes=int(
                raw.get("raw_output_bytes")
                or len(text.encode("utf-8", errors="replace"))
                or 0
            ),
            summary_truncated=bool(raw.get("summary_truncated")),
            raw_artifact_path=str(raw.get("raw_artifact_path") or ""),
        )


@dataclass
class ReviewDecision:
    action: str
    rationale: str = ""
    follow_up_instruction: str = ""
    escalate_to: Optional[Dict[str, str]] = None
    user_question: str = ""
    raw: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "rationale": self.rationale,
            "follow_up_instruction": self.follow_up_instruction,
            "escalate_to": self.escalate_to,
            "user_question": self.user_question,
            "raw": self.raw,
        }

    @staticmethod
    def from_dict(raw: Any) -> "ReviewDecision":
        if not isinstance(raw, dict):
            return ReviewDecision(action=ReviewAction.FAIL.value, rationale="invalid review")
        action = str(raw.get("action") or "").strip().lower()
        if action not in VALID_REVIEW_ACTIONS:
            action = ReviewAction.ASK_USER.value
        esc = raw.get("escalate_to")
        return ReviewDecision(
            action=action,
            rationale=str(raw.get("rationale") or raw.get("reason") or ""),
            follow_up_instruction=str(
                raw.get("follow_up_instruction") or raw.get("follow_up") or ""
            ),
            escalate_to=dict(esc) if isinstance(esc, dict) else None,
            user_question=str(raw.get("user_question") or ""),
            raw=dict(raw),
        )


@dataclass
class WorkerRun:
    run_id: str
    attempt: int
    review_loop: int
    phase: str
    worker_session_id: str
    harness_session_id: str = ""
    prompt: str = ""
    report: Optional[WorkerReport] = None
    result: Optional[Dict[str, Any]] = None
    evidence: Optional[Dict[str, Any]] = None
    started_at: str = ""
    finished_at: str = ""
    economic_source: str = EconomicSource.UNKNOWN.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "attempt": self.attempt,
            "review_loop": self.review_loop,
            "phase": self.phase,
            "worker_session_id": self.worker_session_id,
            "harness_session_id": self.harness_session_id,
            "prompt": self.prompt,
            "report": self.report.to_dict() if self.report else None,
            "result": self.result,
            "evidence": self.evidence,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "economic_source": self.economic_source,
        }

    @staticmethod
    def from_dict(raw: Any) -> Optional["WorkerRun"]:
        if not isinstance(raw, dict):
            return None
        rid = str(raw.get("run_id") or "").strip()
        if not rid:
            return None
        rep = raw.get("report")
        return WorkerRun(
            run_id=rid,
            attempt=int(raw.get("attempt") or 0),
            review_loop=int(raw.get("review_loop") or 0),
            phase=str(raw.get("phase") or ""),
            worker_session_id=str(raw.get("worker_session_id") or ""),
            harness_session_id=str(raw.get("harness_session_id") or ""),
            prompt=str(raw.get("prompt") or ""),
            report=WorkerReport.from_dict(rep) if isinstance(rep, dict) else None,
            result=dict(raw["result"]) if isinstance(raw.get("result"), dict) else None,
            evidence=dict(raw["evidence"]) if isinstance(raw.get("evidence"), dict) else None,
            started_at=str(raw.get("started_at") or ""),
            finished_at=str(raw.get("finished_at") or ""),
            economic_source=str(raw.get("economic_source") or EconomicSource.UNKNOWN.value),
        )


@dataclass
class SupervisedTask:
    """Durable supervised-task record recoverable across Flask restarts."""

    task_id: str
    parent_session_id: str
    coordinator_session_id: str
    worker_session_id: str
    profile_id: str
    phase: str
    user_objective: str
    decision_id: str = ""
    router_strategy: str = RoutingStrategy.SUPERVISED.value
    packet: Optional[DelegationPacket] = None
    runs: List[WorkerRun] = field(default_factory=list)
    reviews: List[Dict[str, Any]] = field(default_factory=list)
    budget: CoordinationBudget = field(default_factory=CoordinationBudget)
    followups_used: int = 0
    coordinator_invocations: int = 0
    events: List[Dict[str, Any]] = field(default_factory=list)
    pending_followups: List[Dict[str, Any]] = field(default_factory=list)
    final_response: str = ""
    created_at: str = ""
    updated_at: str = ""
    project_path: str = ""
    coordinator: Optional[Dict[str, Any]] = None
    worker: Optional[Dict[str, Any]] = None
    paid_approval_granted: bool = False
    cancel_requested: bool = False
    worktree_baseline: Optional[Dict[str, Any]] = None
    delivery: Optional[Dict[str, Any]] = None
    control_events: List[Dict[str, Any]] = field(default_factory=list)
    # Bumped when a worker report becomes reviewable; follow-ups invalidate in-flight reviews.
    report_generation: int = 0
    review_generation: int = 0
    verification_mode: str = ""
    # Canonical chat identity.  Older task files omit these and remain readable.
    parent_user_message_id: Optional[int] = None
    coordinator_response_message_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "parent_session_id": self.parent_session_id,
            "coordinator_session_id": self.coordinator_session_id,
            "worker_session_id": self.worker_session_id,
            "profile_id": self.profile_id,
            "phase": self.phase,
            "user_objective": self.user_objective,
            "decision_id": self.decision_id,
            "router_strategy": self.router_strategy,
            "packet": self.packet.to_dict() if self.packet else None,
            "runs": [r.to_dict() for r in self.runs],
            "reviews": list(self.reviews),
            "budget": self.budget.to_dict(),
            "followups_used": self.followups_used,
            "coordinator_invocations": self.coordinator_invocations,
            "events": list(self.events),
            "pending_followups": list(self.pending_followups),
            "final_response": self.final_response,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "project_path": self.project_path,
            "coordinator": self.coordinator,
            "worker": self.worker,
            "paid_approval_granted": self.paid_approval_granted,
            "cancel_requested": self.cancel_requested,
            "worktree_baseline": self.worktree_baseline,
            "delivery": self.delivery,
            "control_events": list(self.control_events),
            "report_generation": int(self.report_generation or 0),
            "review_generation": int(self.review_generation or 0),
            "verification_mode": self.verification_mode or "",
            "parent_user_message_id": self.parent_user_message_id,
            "coordinator_response_message_id": self.coordinator_response_message_id,
        }

    @staticmethod
    def from_dict(raw: Any) -> Optional["SupervisedTask"]:
        if not isinstance(raw, dict):
            return None
        tid = str(raw.get("task_id") or "").strip()
        if not tid:
            return None
        pkt = raw.get("packet")
        runs_raw = raw.get("runs") if isinstance(raw.get("runs"), list) else []
        runs: List[WorkerRun] = []
        for item in runs_raw:
            wr = WorkerRun.from_dict(item)
            if wr:
                runs.append(wr)
        return SupervisedTask(
            task_id=tid,
            parent_session_id=str(raw.get("parent_session_id") or ""),
            coordinator_session_id=str(raw.get("coordinator_session_id") or ""),
            worker_session_id=str(raw.get("worker_session_id") or ""),
            profile_id=str(raw.get("profile_id") or ""),
            phase=str(raw.get("phase") or TaskPhase.CREATED.value),
            user_objective=str(raw.get("user_objective") or ""),
            decision_id=str(raw.get("decision_id") or ""),
            router_strategy=str(raw.get("router_strategy") or RoutingStrategy.SUPERVISED.value),
            packet=DelegationPacket.from_dict(pkt) if isinstance(pkt, dict) else None,
            runs=runs,
            reviews=list(raw.get("reviews") or []) if isinstance(raw.get("reviews"), list) else [],
            budget=CoordinationBudget.from_dict(raw.get("budget")),
            followups_used=int(raw.get("followups_used") or 0),
            coordinator_invocations=int(raw.get("coordinator_invocations") or 0),
            events=list(raw.get("events") or []) if isinstance(raw.get("events"), list) else [],
            pending_followups=list(raw.get("pending_followups") or [])
            if isinstance(raw.get("pending_followups"), list)
            else [],
            final_response=str(raw.get("final_response") or ""),
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
            project_path=str(raw.get("project_path") or ""),
            coordinator=dict(raw["coordinator"])
            if isinstance(raw.get("coordinator"), dict)
            else None,
            worker=dict(raw["worker"]) if isinstance(raw.get("worker"), dict) else None,
            paid_approval_granted=bool(raw.get("paid_approval_granted")),
            cancel_requested=bool(raw.get("cancel_requested")),
            worktree_baseline=dict(raw["worktree_baseline"])
            if isinstance(raw.get("worktree_baseline"), dict)
            else None,
            delivery=dict(raw["delivery"]) if isinstance(raw.get("delivery"), dict) else None,
            control_events=list(raw.get("control_events") or [])
            if isinstance(raw.get("control_events"), list)
            else [],
            report_generation=int(raw.get("report_generation") or 0),
            review_generation=int(raw.get("review_generation") or 0),
            verification_mode=str(raw.get("verification_mode") or ""),
            parent_user_message_id=raw.get("parent_user_message_id"),
            coordinator_response_message_id=raw.get("coordinator_response_message_id"),
        )
