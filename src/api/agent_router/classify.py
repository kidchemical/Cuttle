"""Fast deterministic turn classifier — the router's first opinion.

An effective router spends its effort on the *kind of work* (which decides
which harness is best at it) and on *scope* (which decides how much model to
spend), not on guessing how hard a question sounds. Most turns are obvious
from their wording: "hello", "write the release notes", "push it", "why does
X fail". Those classify here in microseconds and skip the routing LLM
entirely; only genuinely ambiguous turns pay for the brain.

``classify_turn`` never raises and never touches the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from api.agent_router.types import TaskType

# Fast-path when the strongest lane wins by this confidence; otherwise the
# routing brain is asked and this result is only a hint.
DEFAULT_FAST_PATH_CONFIDENCE = 0.75


@dataclass
class TurnClass:
    task_type: str
    difficulty: str
    confidence: float
    why: str = ""
    signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_type": self.task_type,
            "difficulty": self.difficulty,
            "confidence": round(self.confidence, 3),
            "why": self.why,
            "signals": list(self.signals),
        }


def _rx(*parts: str) -> re.Pattern:
    return re.compile(r"(?:" + "|".join(parts) + r")", re.I)


_GREETING = _rx(
    r"^(?:hi|hey|hello|yo|sup|thanks|thank you|thx|ty|ok|okay|cool|nice|great|"
    r"good (?:morning|night|evening)|test|testing|ping)\b[\s!.?,👋🙂😀]*$",
    r"^(?:this is a test|just testing)\b",
)

# "Add …" / "Fix …" at the start is a change request — but on its own, in a
# short message ("make it pop more", "fix multiplayer"), it says nothing about
# *what*; those stay below the fast-path bar so the brain reads the thread.
_LEADING_IMPERATIVE = (
    r"^(?:please\s+|can you\s+|could you\s+)?(?:add|implement|fix|update|create|build|"
    r"remove|replace|change|make|improve|optimi[sz]e|port|migrate|wire|support|"
    r"speed up|clean up the code)\b"
)
_LEADING_IMPERATIVE_RX = re.compile(_LEADING_IMPERATIVE, re.I)

# Each lane: (strong patterns, weak patterns). Strong = the wording alone
# names the lane; weak = supporting evidence.
_LANES: Dict[str, Dict[str, re.Pattern]] = {
    TaskType.WRITING.value: {
        "strong": _rx(
            r"\b(?:write|draft|compose|polish|proofread|rewrite|reword)\b[^.?!\n]{0,40}"
            r"\b(?:release notes?|changelog|readme|docs?|documentation|guide|blog|post|"
            r"announcement|email|e-mail|letter|tweet|thread|summary|description|"
            r"copy|bio|pitch|proposal|outline|message|reply|commit message|pr description)\b",
            r"\b(?:release notes?|changelog entry|commit message|pr description|"
            r"patch notes|cover letter|press release)\b",
            r"\bsummari[sz]e\b",
            r"\btl;?dr\b",
        ),
        "weak": _rx(r"\b(?:tone|wording|paragraph|prose|headline|tagline|caption)\b"),
    },
    TaskType.OPS.value: {
        "strong": _rx(
            r"^(?:git\s+)?(?:push|pull|rebase|merge|commit|stash|tag)\b",
            r"\b(?:push|commit|rebase|merge|cherry-pick)\b[^.?!\n]{0,20}\b(?:it|this|that|changes|branch|main)\b",
            r"\b(?:restart|reboot|redeploy|deploy|install|uninstall|upgrade|update)\b"
            r"[^.?!\n]{0,25}\b(?:flask|daemon|server|service|workers?|electron|host|app|package|deps|dependencies|cli)\b",
            r"\bself-update\b",
            r"\b(?:clean ?up|delete|archive|prune)\b[^.?!\n]{0,25}\b(?:sessions?|chats?|branches|worktrees?|temp|logs?)\b",
            r"\brun\b[^.?!\n]{0,15}\b(?:the )?(?:tests?|suite|build|script|benchmark)\b",
        ),
        "weak": _rx(r"\b(?:status|logs?|disk|process|port|cron|systemd)\b"),
    },
    TaskType.DEBUGGING.value: {
        "strong": _rx(
            r"\b(?:traceback|exception|stack ?trace|segfault|crash(?:es|ed|ing)?|"
            r"regression|bug|broken|doesn'?t work|does not work|not working|"
            r"stopped working|fails?|failing|failed|error(?:s)?|hangs?|hung|stuck|"
            r"freez(?:e|es|ing)|leak(?:s|ing)?|flaky|wrong (?:output|result)|500|"
            r"race(?: condition)?|intermittent(?:ly)?|deadlocks?|diagnose|root cause|"
            r"occasionally)\b",
        ),
        "weak": _rx(r"\b(?:why|still|again|instead of|expected)\b"),
    },
    TaskType.ARCHITECTURE.value: {
        "strong": _rx(
            r"\b(?:architect(?:ure)?|re-?imagine|redesign|overhaul|rearchitect|"
            r"restructure|design (?:a|the|an)|system design|migration plan|roadmap|"
            r"rfc|trade-?offs?|how should (?:we|i) (?:structure|design|organi[sz]e))\b",
            r"^(?:please\s+)?design\b",
        ),
        "weak": _rx(r"\b(?:plan|approach|strategy|modular|boundar(?:y|ies)|layers?|scal(?:e|ing))\b"),
    },
    TaskType.CODING.value: {
        "strong": _rx(
            r"\b(?:implement|add|build|create|make|refactor|rename|extract|wire|hook up|"
            r"port|convert|migrate|patch|fix|change|update|remove|delete|replace|support)\b"
            r"[^.?!\n]{0,60}\b(?:function|method|class|module|endpoint|route|api|test|tests|"
            r"component|button|page|ui|feature|flag|setting|option|field|column|schema|"
            r"script|file|cli|command|handler|hook|config|bug|chip|badge|modal|panel|tab)s?\b",
            r"\b(?:write|add) (?:a |some )?(?:unit |integration |e2e )?tests?\b",
            r"\b(?:refactor|rename)\b",
            _LEADING_IMPERATIVE,
            r"\b(?:fix|change|update|edit|add|remove|rename)\b[^\n]{0,50}"
            r"[\w/.-]+\.(?:py|js|ts|tsx|css|html|json|yaml|yml|sh)\b",
        ),
        "weak": _rx(
            r"\b(?:code|python|javascript|typescript|css|html|sql|regex|json|yaml)\b",
            r"[\w/.-]+\.(?:py|js|ts|tsx|css|html|json|yaml|yml|md|sh)\b",
        ),
    },
    TaskType.RESEARCH.value: {
        "strong": _rx(
            r"\b(?:compare|comparison|alternatives? to|vs\.?|versus|which (?:library|tool|model|framework|provider|service)|"
            r"best (?:way|library|tool|model|practice)s? (?:to|for)|look up|search (?:the )?web|"
            r"find (?:out|me|docs)|latest (?:version|release|news)|pricing|benchmarks?)\b",
        ),
        "weak": _rx(r"\b(?:docs for|documentation for|options for|pros and cons)\b"),
    },
    TaskType.EXPLAIN.value: {
        "strong": _rx(
            r"^(?:why|how|what|where|when|which|who)\b[^\n]{0,160}\b(?:this|that|it|our|my|the)\b"
            r"[^\n]{0,80}\b(?:code|repo|codebase|function|file|module|router|chat|session|"
            r"cuttle|daemon|flask|worker|pipeline|config|setting|log|turn|agent|harness|ui|page)s?\b",
            r"\b(?:explain|walk me through|what does|how does|where is|where does|trace)\b",
            r"\bCH-\d{3,}",
            r"\bsummari[sz]e\b[^\n]{0,40}\b(?:this|the|our) (?:project|repo|codebase|code|file|module|function|class|architecture)\b",
        ),
        "weak": _rx(r"\?\s*$", r"\b(?:understand|meaning|purpose)\b"),
    },
}

# Strong-lane priority when several fire (more specific first): a fix
# request that mentions an error is debugging, not coding; "write the docs"
# is writing even though "write" is a coding verb.
_PRIORITY = [
    TaskType.ARCHITECTURE.value,
    TaskType.OPS.value,
    TaskType.WRITING.value,
    TaskType.DEBUGGING.value,
    TaskType.CODING.value,
    TaskType.RESEARCH.value,
    TaskType.EXPLAIN.value,
]

_SCOPE_SMALL = _rx(
    r"\b(?:quick(?:ly)?|small|tiny|simple|typo|one[- ]liner|rename|bump|"
    r"single|minor|real quick|briefly|short|"
    # small artifacts: a change to one of these is a quick edit
    r"docstring|comment|log(?: line)?|heading|label|tooltip|placeholder|string|"
    r"colou?r|icon|padding|margin|spacing)\b"
)
_SCOPE_BROAD = _rx(
    r"\b(?:everything|entire|whole|across|all (?:the )?(?:files|modules|places|pages)|"
    r"end[- ]to[- ]end|from scratch|re-?imagine|overhaul|redesign|rewrite (?:the|it)|"
    r"in general|many bugs|lots of|deep(?:ly)?|thorough(?:ly)?|comprehensive|"
    r"multiple|several|production|everywhere|while preserving|without breaking|"
    r"all existing|self-adjusting)\b"
)


def _scope(text: str, task_type: str) -> str:
    words = len(text.split())
    broad = len(_SCOPE_BROAD.findall(text)) + (1 if text.count(",") >= 3 else 0)
    small = bool(_SCOPE_SMALL.search(text))
    asks = len(re.findall(r"[?]|\b(?:also|and then|plus|additionally)\b", text, re.I))
    if task_type == TaskType.BASIC_ASK.value:
        return "low"
    if broad >= 2 or (broad and words > 40) or words > 160 or (asks >= 3 and words > 60):
        return "high"
    if task_type == TaskType.ARCHITECTURE.value:
        # Design work is deep by nature unless explicitly small.
        return "medium" if small and not broad else "high"
    if small and not broad:
        return "low"
    if task_type in (TaskType.EXPLAIN.value, TaskType.OPS.value, TaskType.RESEARCH.value) and words <= 25:
        return "low"
    return "medium"


def classify_turn(message: str) -> TurnClass:
    """Classify one user turn. Pure; never raises."""
    text = (message or "").strip()
    if not text:
        return TurnClass(TaskType.OTHER.value, "low", 0.0, "empty")

    if _GREETING.search(text):
        return TurnClass(TaskType.BASIC_ASK.value, "low", 0.97, "greeting / smoke test", ["greeting"])

    strong: Dict[str, List[str]] = {}
    weak: Dict[str, int] = {}
    for lane, pats in _LANES.items():
        hits = [m.group(0) for m in pats["strong"].finditer(text)]
        if hits:
            strong[lane] = hits
        w = len(pats["weak"].findall(text))
        if w:
            weak[lane] = w

    words = len(text.split())
    if not strong:
        # Short question with no repo/change/ops wording → plain chat.
        if words <= 20 and not weak.get(TaskType.CODING.value):
            # A short imperative ("make it pop more") is usually a follow-up
            # whose meaning lives in the thread — let the brain look.
            conf = 0.8 if text.endswith("?") else 0.55
            return TurnClass(
                TaskType.BASIC_ASK.value, "low", conf, "short question, no task wording", ["short"]
            )
        lane = max(weak, key=weak.get) if weak else TaskType.OTHER.value
        return TurnClass(
            lane, _scope(text, lane), 0.35, "no strong signal", [f"weak:{k}" for k in weak]
        )

    ordered = [lane for lane in _PRIORITY if lane in strong]
    lane = ordered[0]
    # Debugging + an explicit change request is a debugging turn; explain +
    # a change request is coding (the user wants it changed, not explained).
    if lane == TaskType.EXPLAIN.value and TaskType.CODING.value in strong:
        lane = TaskType.CODING.value
    # "Summarize this project" is a question about the code, not a prose deliverable.
    if (
        lane == TaskType.WRITING.value
        and TaskType.EXPLAIN.value in strong
        and all(re.match(r"summari[sz]e", h.strip(), re.I) for h in strong[lane])
    ):
        lane = TaskType.EXPLAIN.value
    conf = 0.82
    if len(ordered) == 1:
        conf = 0.9
    elif len(ordered) >= 3:
        conf = 0.7
    conf = min(0.97, conf + 0.03 * min(weak.get(lane, 0), 2))
    if words <= 6 and all(_LEADING_IMPERATIVE_RX.match(h.strip()) for h in strong.get(lane, [])):
        conf = min(conf, 0.6)
    hit = strong.get(lane, [""])[0].strip()
    signals = [f"{k}:{v[0].strip()[:40]}" for k, v in strong.items()]
    return TurnClass(lane, _scope(text, lane), conf, f"matched “{hit[:48]}”", signals)


def classifier_settings(raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """``settings.json → agent_router.classifier`` with defaults."""
    if raw is None:
        try:
            from managers.settings_manager import get_settings_manager

            ar = get_settings_manager().get_setting("agent_router") or {}
            raw = ar.get("classifier") if isinstance(ar, dict) else None
        except Exception:
            raw = None
    raw = raw if isinstance(raw, dict) else {}
    try:
        threshold = float(raw.get("fast_path_confidence", DEFAULT_FAST_PATH_CONFIDENCE))
    except (TypeError, ValueError):
        threshold = DEFAULT_FAST_PATH_CONFIDENCE
    return {
        "fast_path": raw.get("fast_path", True) is not False,
        "fast_path_confidence": max(0.0, min(1.0, threshold)),
    }


def save_classifier_settings(raw: Any) -> "tuple[Dict[str, Any], Optional[str]]":
    """Validate + persist ``agent_router.classifier``. Returns (settings, error)."""
    if not isinstance(raw, dict):
        return classifier_settings(), "classifier must be an object"
    clean = classifier_settings({**classifier_settings(), **raw})
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    try:
        sm.reload()
    except Exception:
        pass
    ar = sm.get_setting("agent_router") or {}
    ar = ar if isinstance(ar, dict) else {}
    ar["classifier"] = clean
    sm.set_setting("agent_router", ar)
    return clean, None
