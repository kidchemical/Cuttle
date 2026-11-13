"""Cursor chip labels must match across composer, queue, and bubbles."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
CONTROLS_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_agent_controls.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


@node_only
def test_cursor_chip_display_label_is_canonical():
    # Sliced into CuttleChatAgentControls (chat_agent_controls.js) — Slice 2A
    # stage 3; the controller owns Cursor label/collapse decisions.
    harness = f"""
const C = require({str(CONTROLS_JS)!r});
const state = {{
    preferredModel: 'auto',
    cursorModels: [
        {{ id: 'auto', label: 'Auto' }},
        {{ id: 'gpt-5.6', label: 'GPT-5.6' }},
    ],
}};
const ctl = C.create({{ state: state, getSessionId: () => null }});
process.stdout.write(JSON.stringify({{
    auto: ctl.cursorChipDisplayLabel('auto'),
    gpt: ctl.cursorChipDisplayLabel('gpt-5.6'),
    bare: ctl.cursorChipDisplayLabel(''),
}}));
"""
    import subprocess as _sp

    proc = _sp.run(
        ["node", "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["auto"] == "Cursor - Auto", res
    assert res["gpt"] == "Cursor - GPT-5.6", res
    assert res["bare"] == "Cursor - Auto", res
    assert "Agent" not in res["auto"]
    assert "·" not in res["auto"]


@node_only
def test_composer_does_not_use_agent_middot_format():
    """Guard against regressing to 'Cursor Agent · Auto' in renderSlashChips."""
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "chip.label + ' · ' + preferredModelLabel" not in src
    assert "cursorChipDisplayLabel(preferred)" in src
    # User-bubble display dispatch lives in the owned Messages module
    # (Phase 3 Slice 8B); the canonical collapse call moved with it.
    mod = (REPO_ROOT / "src" / "web" / "js" / "chat/chat_messages.js").read_text(
        encoding="utf-8"
    )
    assert "collapseCursorSlashChips(parsed.chips)" in mod


@node_only
def test_claude_badge_never_repeats_agent_name_or_strips_stored_model():
    """CH-002404-131: effort-only pins rendered "Claude Code - Claude Code · medium"."""
    model_js = REPO_ROOT / "src" / "web" / "js" / "chat/chat_agent_model.js"
    harness = f"""
require({str(model_js)!r});
const C = require({str(CONTROLS_JS)!r});
const ctl = C.create({{ state: {{ claudeModels: [] }}, getSessionId: () => null }});
const stored = {{ chips: [{{
    label: 'Claude Code - Opus 5.5 · medium',
    meta: '/claude · model opus · effort medium',
    category: 'claude',
}}] }};
const bare = {{ chips: [{{ label: 'Claude Code', meta: '/claude', category: 'claude' }}] }};
process.stdout.write(JSON.stringify({{
    stored: ctl.enrichSlashCommandWithClaudeModel(stored, '', 'medium').chips[0].label,
    bare: ctl.enrichSlashCommandWithClaudeModel(bare, '', 'medium').chips[0].label,
}}));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["stored"] == "Claude Code - Opus 5.5 · medium", res
    assert res["bare"] == "Claude Code · medium", res
