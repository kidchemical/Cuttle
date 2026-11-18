"""Studio work panel CSS (L4): every rule is Studio-scoped; the classes it styles exist in the owners."""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"
CSS = WEB / "css" / "studio" / "work_panel.css"
PANEL_JS = WEB / "js" / "chat" / "chat_work_panel.js"
TASKS_JS = WEB / "js" / "gizmos" / "tasks_gizmo.js"
FLEET_JS = WEB / "js" / "chat" / "chat_subagent_fleet.js"
LAUNCHERS_JS = WEB / "js" / "chat" / "chat_subagent_launchers.js"
ORCH_JS = WEB / "js" / "chat" / "chat_orchestration.js"


def _style_rules(css: str) -> list[str]:
    """Selectors of style rules (keyframe steps and at-rule preludes skipped)."""
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = []
    for prelude in re.findall(r"([^{};]+)\{", body):
        text = prelude.strip()
        if not text or text.startswith("@"):
            continue
        if re.fullmatch(r"(from|to|[\d.%,\s]+)", text):
            continue  # keyframe steps
        selectors.extend(part.strip() for part in text.split(","))
    return selectors


def test_every_rule_is_studio_scoped():
    rules = _style_rules(CSS.read_text(encoding="utf-8"))
    assert rules
    for sel in rules:
        assert sel.startswith(":root.cuttle-studio"), sel


def test_styled_classes_are_emitted_by_the_owners():
    css = CSS.read_text(encoding="utf-8")
    owners = "\n".join(p.read_text(encoding="utf-8") for p in (PANEL_JS, TASKS_JS, FLEET_JS, LAUNCHERS_JS, ORCH_JS))
    for cls in ("work-panel", "work-panel-cols", "work-panel-tasks", "work-panel-cards", "work-list",
                "work-list-head", "work-task-list", "is-in-progress", "is-entering", "studio-fleet-card",
                "studio-agent-header", "studio-working-tag", "studio-live-dot", "chat-orch-quick"):
        assert re.search(r"\." + re.escape(cls) + r"\b", css), f"css lost {cls}"
        assert cls in owners, f"no owner emits {cls}"
