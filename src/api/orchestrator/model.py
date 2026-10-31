"""Orchestrator playbooks — pure contract (no Cuttle imports, no I/O).

A playbook is a versioned JSON document describing *how* an orchestrator chat
delegates: an ordered org chart of tiers (each a list of members), grind/retry
limits, supervisor defaults, and free-form notes. A member is a saved agent
profile (``{"profile": id}``) or a temporary agent pinned inline
(``{"name", "agent", "model", "effort"}``); either can be switched off
(``"on": false``) without removing it. A tier with no member switched on is
left out of the brief. The host injects resolved profiles; this module only
normalizes documents and renders text:

- ``normalize_playbook(raw)``  → canonical dict (raises ``PlaybookError``)
- ``render_brief(playbook, profiles, ...)`` → markdown the orchestrator follows
- ``model_tiers(playbook, profiles)`` → ``--model-tiers`` mapping for supervision
- ``supervisor_argv(playbook, target, profiles)`` → ``chat_cli supervise`` args

Schema version 2. Version 1 tiers (``profiles: [id, …]``) are read as
members. Unknown keys are dropped; missing keys take defaults.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional

SCHEMA_VERSION = 2
MAX_MEMBERS = 6
ROLES = ("worker", "escalation", "expert")
_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/@+-]{1,200}$")

DEFAULT_LIMITS: Dict[str, int] = {
    # Attempts / minutes the orchestrator may spend on one problem in its own
    # turn before it must delegate.
    "solo_attempts": 2,
    "solo_minutes": 40,
    # Sharper-brief retries on the same tier before escalating a child task.
    "child_retries": 1,
    # Setup failures ("was not started", bad pin) on one spec before reporting.
    "setup_retries": 2,
}
_LIMIT_BOUNDS = {
    "solo_attempts": (1, 20),
    "solo_minutes": (5, 600),
    "child_retries": (0, 5),
    "setup_retries": (1, 5),
}
DEFAULT_SUPERVISOR: Dict[str, Any] = {
    "every_sec": 900,
    "agent": "",
    "instructions": (
        "Keep the orchestrator on its Tasks list; nudge it when it stalls; "
        "enforce the org chart."
    ),
    "require_branch": "",
    "require_path": "",
    "forbid_branches": [],
}


class PlaybookError(ValueError):
    """Invalid playbook document."""


def _text(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _slug(value: Any) -> str:
    return _text(value, 40).lower().replace(" ", "-")


def default_playbook() -> Dict[str, Any]:
    """Seed playbook: budget workers → escalation → frontier experts."""
    return normalize_playbook({
        "id": "default",
        "name": "Default org chart",
        "description": "Budget agents grind, Grok escalates, frontier models plan and review.",
        "tiers": [
            {"id": "worker", "label": "Grunt", "role": "worker",
             "members": ["grunt", "grunt-muse"],
             "use_for": "Implementation, edits, test runs, searches, log digging, mechanical refactors, docs."},
            {"id": "escalate", "label": "Escalation", "role": "escalation",
             "members": ["escalate"],
             "use_for": "A worker task failed after a sharper retry, came back wrong, or is clearly beyond the worker tier."},
            {"id": "expert", "label": "Expert", "role": "expert",
             "members": ["expert-claude", "expert-codex"],
             "use_for": "Critical planning, architecture decisions, final review of risky diffs. Plans and reviews only."},
        ],
    })


def normalize_playbook(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PlaybookError("playbook must be a JSON object")
    pid = _slug(raw.get("id"))
    if not _ID_RE.match(pid):
        raise PlaybookError("playbook id must be 1–40 chars: a letter, then a-z 0-9 _ -")
    name = _text(raw.get("name"), 80) or pid
    tiers_raw = raw.get("tiers")
    if not isinstance(tiers_raw, list) or not tiers_raw:
        raise PlaybookError("playbook needs at least one tier")
    if len(tiers_raw) > 8:
        raise PlaybookError("at most 8 tiers")
    tiers: List[Dict[str, Any]] = []
    seen = set()
    for index, tier in enumerate(tiers_raw, start=1):
        if not isinstance(tier, Mapping):
            raise PlaybookError(f"tier {index} must be an object")
        tid = _slug(tier.get("id")) or f"tier-{index}"
        if not _ID_RE.match(tid) or tid in seen:
            raise PlaybookError(f"tier {index}: id {tid!r} is invalid or duplicated")
        seen.add(tid)
        role = _text(tier.get("role"), 20).lower() or "worker"
        if role not in ROLES:
            raise PlaybookError(f"tier {tid}: role must be one of {', '.join(ROLES)}")
        members = _normalize_members(tier, tid)
        tiers.append({
            "id": tid,
            "label": _text(tier.get("label"), 60) or tid,
            "role": role,
            "members": members,
            "use_for": _text(tier.get("use_for"), 600),
        })
    if not any(t["role"] == "worker" for t in tiers):
        raise PlaybookError("playbook needs a worker tier (where every task starts)")
    if not any(t["role"] == "worker" for t in active_tiers({"tiers": tiers})):
        raise PlaybookError("switch on at least one agent in a worker tier (where every task starts)")

    limits = dict(DEFAULT_LIMITS)
    limits_raw = raw.get("limits") if isinstance(raw.get("limits"), Mapping) else {}
    for key, (low, high) in _LIMIT_BOUNDS.items():
        if key in limits_raw and limits_raw[key] not in (None, ""):
            try:
                value = int(limits_raw[key])
            except (TypeError, ValueError):
                raise PlaybookError(f"limits.{key} must be a whole number") from None
            if not low <= value <= high:
                raise PlaybookError(f"limits.{key} must be between {low} and {high}")
            limits[key] = value

    sup = copy.deepcopy(DEFAULT_SUPERVISOR)
    sup_raw = raw.get("supervisor") if isinstance(raw.get("supervisor"), Mapping) else {}
    if "every_sec" in sup_raw and sup_raw["every_sec"] not in (None, ""):
        try:
            every = int(sup_raw["every_sec"])
        except (TypeError, ValueError):
            raise PlaybookError("supervisor.every_sec must be a whole number") from None
        if not 60 <= every <= 86400:
            raise PlaybookError("supervisor.every_sec must be between 60 and 86400")
        sup["every_sec"] = every
    if "agent" in sup_raw:
        sup["agent"] = _slug(sup_raw.get("agent"))
    if "instructions" in sup_raw:
        sup["instructions"] = _text(sup_raw.get("instructions"), 2000)
    if "require_path" in sup_raw:
        sup["require_path"] = _text(sup_raw.get("require_path"), 500)
    branch = _text(sup_raw.get("require_branch"), 200)
    if branch and not _BRANCH_RE.match(branch):
        raise PlaybookError("supervisor.require_branch is not a valid branch name")
    sup["require_branch"] = branch
    forbid_raw = sup_raw.get("forbid_branches") or []
    if isinstance(forbid_raw, str):
        forbid_raw = [b for b in re.split(r"[\s,]+", forbid_raw) if b]
    if not isinstance(forbid_raw, list):
        raise PlaybookError("supervisor.forbid_branches must be a list")
    forbid = list(dict.fromkeys(_text(b, 200) for b in forbid_raw if _text(b, 200)))
    for b in forbid:
        if not _BRANCH_RE.match(b):
            raise PlaybookError(f"supervisor.forbid_branches: {b!r} is not a valid branch name")
    sup["forbid_branches"] = forbid[:20]

    return {
        "schema": SCHEMA_VERSION,
        "id": pid,
        "name": name,
        "description": _text(raw.get("description"), 400),
        "tiers": tiers,
        "limits": limits,
        "supervisor": sup,
        "notes": _text(raw.get("notes"), 8000),
    }


def _normalize_members(tier: Mapping[str, Any], tid: str) -> List[Dict[str, Any]]:
    raw = tier.get("members")
    if raw is None:
        raw = tier.get("profiles") or []  # schema 1: a list of profile ids
    if not isinstance(raw, list):
        raise PlaybookError(f"tier {tid}: members must be a list")
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            if not _slug(item):
                continue
            item = {"profile": item}
        if not isinstance(item, Mapping):
            raise PlaybookError(f"tier {tid}: each member must be a profile id or an object")
        on = item.get("on") is not False
        pid = _slug(item.get("profile"))
        if pid:
            member: Dict[str, Any] = {"profile": pid, "on": on}
            key = ("profile", pid)
        else:
            agent = _slug(item.get("agent"))
            if not agent or agent == "none":
                raise PlaybookError(f"tier {tid}: a temporary agent needs a harness")
            model = _text(item.get("model"), 120)
            effort = _text(item.get("effort"), 20).lower()
            name = _text(item.get("name"), 60) or " ".join(x for x in (agent, model, effort) if x)
            member = {"name": name, "agent": agent, "model": model, "effort": effort, "on": on}
            key = ("agent", agent, model, effort)
        if key in seen:
            continue
        seen.add(key)
        out.append(member)
    if not out:
        raise PlaybookError(f"tier {tid}: add at least one agent")
    if len(out) > MAX_MEMBERS:
        raise PlaybookError(f"tier {tid}: at most {MAX_MEMBERS} agents")
    return out


def is_temporary(member: Mapping[str, Any]) -> bool:
    return not member.get("profile")


def active_members(tier: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(m) for m in tier.get("members") or [] if m.get("on", True) is not False]


def active_tiers(playbook: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Tiers with at least one member switched on, members filtered to those."""
    out = []
    for tier in playbook.get("tiers") or []:
        members = active_members(tier)
        if members:
            out.append({**tier, "members": members})
    return out


def referenced_profiles(playbook: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for tier in playbook.get("tiers") or []:
        for member in tier.get("members") or []:
            pid = member.get("profile")
            if pid and pid not in out:
                out.append(pid)
    return out


def temporary_agents(playbook: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(m) for tier in playbook.get("tiers") or []
            for m in tier.get("members") or [] if is_temporary(m)]


def missing_profiles(playbook: Mapping[str, Any], known: Iterable[str]) -> List[str]:
    known_set = set(known)
    return [pid for pid in referenced_profiles(playbook) if pid not in known_set]


def describe_profile(profile: Optional[Mapping[str, Any]]) -> str:
    """``cursor auto`` / ``claude claude-opus-5-5 (high)`` / ``(missing)``."""
    if not profile:
        return "(missing profile)"
    agent = str(profile.get("agent") or "none")
    parts = ["any harness" if agent == "none" else agent]
    model = str(profile.get("model") or "")
    if model:
        parts.append(model)
    text = " ".join(parts)
    effort = str(profile.get("effort") or "")
    return f"{text} ({effort})" if effort else text


def describe_member(member: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]]) -> str:
    if is_temporary(member):
        return describe_profile(member)
    return describe_profile(profiles.get(member.get("profile")))


def spawn_profile(member: Mapping[str, Any]) -> Any:
    """The child spec's ``profile`` value: a saved id, or an inline (ephemeral) object."""
    if not is_temporary(member):
        return member["profile"]
    return {k: member[k] for k in ("name", "agent", "model", "effort") if member.get(k)}


def model_tiers(
    playbook: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]]
) -> Dict[str, List[str]]:
    """Tier id → human pin strings, the shape ``chat_cli supervise --model-tiers`` takes."""
    return {
        tier["id"]: [describe_member(m, profiles) for m in tier["members"]]
        for tier in active_tiers(playbook)
    }


def supervisor_argv(
    playbook: Mapping[str, Any],
    target: str,
    profiles: Mapping[str, Mapping[str, Any]],
) -> List[str]:
    """Arguments after ``python -m api.chat_cli`` that start supervision of ``target``."""
    sup = playbook.get("supervisor") or DEFAULT_SUPERVISOR
    argv = ["supervise", target, "--every", str(int(sup.get("every_sec") or 900))]
    # No --agent unless the playbook pins one: check-ins then follow the
    # supervisor chat's own chip instead of spending a pinned harness's quota.
    if sup.get("agent"):
        argv += ["--agent", str(sup["agent"])]
    if sup.get("instructions"):
        argv += ["--instructions", str(sup["instructions"])]
    if sup.get("require_branch"):
        argv += ["--require-branch", str(sup["require_branch"])]
    if sup.get("require_path"):
        argv += ["--require-path", str(sup["require_path"])]
    for branch in sup.get("forbid_branches") or []:
        argv += ["--forbid-branch", str(branch)]
    argv += ["--model-tiers", json.dumps(model_tiers(playbook, profiles), separators=(",", ":"))]
    argv.append("--json")
    return argv


def render_brief(
    playbook: Mapping[str, Any],
    profiles: Mapping[str, Mapping[str, Any]],
    *,
    chat_handle: str = "",
    goal: str = "",
    spawn_cli: str = "python -m api.subagents",
    brief_cli: str = "python -m api.orchestrator",
) -> str:
    """Markdown brief for an orchestrator chat. Deterministic for a given input."""
    limits = {**DEFAULT_LIMITS, **(playbook.get("limits") or {})}
    tiers = active_tiers(playbook) or list(playbook.get("tiers") or [])
    worker = next((t for t in tiers if t["role"] == "worker"), tiers[0])
    lines: List[str] = [
        f"## Orchestrator playbook: {playbook.get('name') or playbook.get('id')}",
        "",
        (f"This chat ({chat_handle}) is an orchestrator. " if chat_handle else "This chat is an orchestrator. ")
        + "Plan, delegate, verify and integrate. These are standing instructions for the whole chat; "
        "they outrank the habit of doing the work yourself.",
    ]
    if goal:
        lines += ["", f"Goal: {goal}"]
    lines += [
        "",
        f"Delegate only through real child chats: `{spawn_cli} spawn --parent <this chat> --wait --json "
        "--child '{\"title\":…,\"profile\":\"<id>\",\"message\":…}'`. Never use your own CLI's built-in "
        "subagent/task tool. The profile pins harness, model and effort; do not restate them on the child. "
        "A temporary agent has no saved profile: its cell shows the exact `profile` object to send.",
        "",
        "### Org chart (in escalation order)",
        "",
        "| Tier | Role | Agents | Use for |",
        "|---|---|---|---|",
    ]
    for tier in tiers:
        cells = ", ".join(
            (f"`{json.dumps(spawn_profile(m), separators=(',', ':'), ensure_ascii=False)}` (temporary)"
             if is_temporary(m) else f"`{m['profile']}`")
            + f": {describe_member(m, profiles)}"
            for m in tier["members"]
        )
        use_for = (tier.get("use_for") or "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {tier['label']} | {tier['role']} | {cells} | {use_for} |")
    lines += [
        "",
        "### Rules",
        "",
        f"1. **Default down.** Every task starts at the {worker['label']} tier unless it is planning or "
        "review that needs expert judgment. Use the first agent in a tier; the others are alternates "
        "(parallel lane, or when the first is out of usage).",
        f"2. **Escalate on evidence, one tier at a time.** Retry a failed child task "
        f"{limits['child_retries']}× on the same tier with a sharper brief, then move to the next tier. "
        "Say why in one line when you escalate.",
        f"3. **Don't grind it yourself.** After {limits['solo_attempts']} failed attempts or about "
        f"{limits['solo_minutes']} minutes on one problem in your own turn, delegate it to the worker "
        "tier with what you tried, then escalate if that child fails.",
        "4. **Never resend an identical failing spawn.** A setup error (\"was not started\", unsupported "
        "model/effort, missing CLI, quota) means fix the spec or switch to the tier's next agent. After "
        f"{limits['setup_retries']} setup failures on one task, report it instead of looping.",
        "5. **Experts plan and review; they do not grind.** Send them the plan, the diff, or a specific "
        "question. For high-stakes plans or merges, ask every expert agent (`--collect all`) and reconcile.",
        "6. **Self-contained briefs.** Children start cold: give the goal, file paths, constraints (branch, "
        "owner module, tests to run) and the exact deliverable.",
        "7. **Verify before you trust.** Run the tests or read the diff before marking work done.",
        "8. **Parallelize independent lanes** (`--collect all`); use `--collect serial` when lanes touch the "
        "same files; `--lifetime conversational` when iterating with one child.",
        "9. **No silent stops.** Keep a Tasks gizmo with the high-level plan and patch it with "
        "`set_done`/`set_undone`/`set_text`/`add`/`remove`/`description` only — never send `items`, which "
        "replaces the whole list. If blocked, ask with one Q&A action form and leave open items with a reason.",
        "10. A supervisor chat may steer or queue messages into this chat; treat them as the user's "
        "standing nudges, not new scope.",
    ]
    notes = str(playbook.get("notes") or "").strip()
    if notes:
        lines += ["", "### Playbook notes", "", notes]
    lines += [
        "",
        f"Reprint this brief any time: `{brief_cli} brief`"
        + (f" --session {chat_handle}" if chat_handle else "")
        + ". Edit the playbook in Apps → Orchestrator and agent profiles in Apps → Agents.",
    ]
    return "\n".join(lines).strip() + "\n"
