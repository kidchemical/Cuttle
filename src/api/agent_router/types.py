"""Typed entities for the Cuttle agent router layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class RouterMode(str, Enum):
    OFF = "off"
    API = "api"
    LOCAL = "local"
    AGENT = "agent"


class TaskType(str, Enum):
    """The *kind of work* — what decides which harness is best at a turn.

    Difficulty is scope (one quick step / normal / broad or ambiguous), not
    "how impressive the question sounds".
    """

    BASIC_ASK = "basic_ask"  # conversation, quick general question, no repo access
    EXPLAIN = "explain"  # question about this codebase/system/logs; read, don't change
    CODING = "coding"  # implement or change code
    DEBUGGING = "debugging"  # something is broken: find the cause and fix it
    ARCHITECTURE = "architecture"  # design / plan / cross-cutting restructure
    RESEARCH = "research"  # external lookup, compare libraries/tools/options
    WRITING = "writing"  # prose deliverable: docs, release notes, summaries, emails
    OPS = "ops"  # operate things: git, restart, deploy, workers, installs, chores
    OTHER = "other"


class Difficulty(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FailureKind(str, Enum):
    NONE = "none"
    TRANSPORT = "transport"
    TASK = "task"
    # User pressed Stop / the chat went away. Terminal: never escalate or fall back.
    CANCELLED = "cancelled"


class TargetSource(str, Enum):
    ROUTER = "router"
    DEFAULT = "default"
    SESSION = "session"
    STARRED = "starred"
    MANUAL_OVERRIDE = "manual_override"
    ESCALATION = "escalation"
    FALLBACK = "fallback"
    RETRY = "retry"


VALID_TASK_TYPES = frozenset(t.value for t in TaskType)
VALID_DIFFICULTIES = frozenset(d.value for d in Difficulty)
VALID_MODES = frozenset(m.value for m in RouterMode)


@dataclass(frozen=True)
class ExecutionTarget:
    agent: str
    model: str
    # Reasoning effort for harnesses that take one (`--effort` / `--reasoning-effort`);
    # empty = the agent's own default. Not part of ``key()``: demotions, quota and
    # dedupe are per model, whatever effort it runs at.
    effort: str = ""

    def key(self) -> str:
        return f"{self.agent}:{self.model}"

    def to_dict(self) -> Dict[str, str]:
        d = {"agent": self.agent, "model": self.model}
        if self.effort:
            d["effort"] = self.effort
        return d

    @staticmethod
    def from_dict(raw: Any) -> Optional["ExecutionTarget"]:
        if not isinstance(raw, dict):
            return None
        agent = str(raw.get("agent") or "").strip().lower()
        model = str(raw.get("model") or "").strip()
        effort = str(raw.get("effort") or "").strip().lower()
        if not agent:
            return None
        return ExecutionTarget(agent=agent, model=model, effort=effort)


@dataclass
class RouterProviderConfig:
    """How the routing brain itself is invoked (not the task executor)."""

    mode: str = RouterMode.API.value
    # api
    api_provider: str = "openai"
    api_model: str = "gpt-4o-mini"
    # local / OpenAI-compatible
    local_endpoint: str = ""
    local_model: str = ""
    # agent CLI as routing brain
    agent_id: str = "cursor"
    agent_model: str = "auto"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "api_provider": self.api_provider,
            "api_model": self.api_model,
            "local_endpoint": self.local_endpoint,
            "local_model": self.local_model,
            "agent_id": self.agent_id,
            "agent_model": self.agent_model,
        }


@dataclass
class FallbackPolicy:
    ordered: List[ExecutionTarget] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"ordered": [t.to_dict() for t in self.ordered]}


@dataclass
class RouterConfig:
    provider: RouterProviderConfig = field(default_factory=RouterProviderConfig)
    default_target: ExecutionTarget = field(
        default_factory=lambda: ExecutionTarget(agent="cursor", model="auto")
    )
    escalation_target: ExecutionTarget = field(
        default_factory=lambda: ExecutionTarget(agent="cursor", model="grok-4.6")
    )
    fallbacks: FallbackPolicy = field(default_factory=FallbackPolicy)

    def enabled(self) -> bool:
        return (self.provider.mode or RouterMode.OFF.value) != RouterMode.OFF.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider.to_dict(),
            "default_target": self.default_target.to_dict(),
            "escalation_target": self.escalation_target.to_dict(),
            "fallbacks": self.fallbacks.to_dict(),
        }


@dataclass
class RoutingContext:
    user_request: str
    project_id: str = ""
    project_name: str = ""
    project_path: str = ""
    code_changes_requested: bool = False
    explicit_constraints: str = ""
    session_id: Any = None
    available_targets: List[ExecutionTarget] = field(default_factory=list)


@dataclass
class RoutingDecision:
    decision_id: str
    task_type: str
    difficulty: str
    target: ExecutionTarget
    confidence: float
    reason: str
    escalation_target: ExecutionTarget
    source: str = TargetSource.ROUTER.value
    # Typed routing strategy — never encode supervised mode as a fake agent name.
    strategy: str = "direct"
    supervised_profile: Optional[str] = None
    # Use-case table may carry its own ordered fallback chain (None = config chain).
    fallbacks: Optional[List[ExecutionTarget]] = None
    raw: Optional[Dict[str, Any]] = None

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "decision_id": self.decision_id,
            "task_type": self.task_type,
            "difficulty": self.difficulty,
            "target_agent": self.target.agent,
            "target_model": self.target.model,
            "confidence": self.confidence,
            "reason": self.reason,
            "escalation_target": self.escalation_target.to_dict(),
            "source": self.source,
            "strategy": self.strategy or "direct",
        }
        if self.fallbacks is not None:
            d["fallbacks"] = [t.to_dict() for t in self.fallbacks]
        if self.supervised_profile:
            d["supervised_profile"] = self.supervised_profile
        return d


@dataclass
class ExecutionOutcome:
    success: bool
    failure_kind: str = FailureKind.NONE.value
    response: str = ""
    target: Optional[ExecutionTarget] = None
    decision_id: Optional[str] = None
    source: str = TargetSource.ROUTER.value
    error: str = ""
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "failure_kind": self.failure_kind,
            "target": self.target.to_dict() if self.target else None,
            "decision_id": self.decision_id,
            "source": self.source,
            "error": self.error,
            "attempts": list(self.attempts),
        }


def dataclass_to_plain(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    return obj
