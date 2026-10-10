"""Bare sticky-agent send must not submit or unpin the chip.

Reproduces: Enter with only a sticky agent badge (/cursor, /codex, …) and no
textarea body.

Two coupled bugs observed in chat:

1. **Blank send** — ``composeMessageWithSlashChip`` turns chip + empty body into
   ``"/cursor"``. ``sendMessage`` / ``sendWelcomeMessage`` only guard
   ``if (!message)``, so the bare agent token is treated as content and is
   sent (or queued while generating).

2. **Sticky unpin** — that composed token is trimmed (no trailing space), but
   ``getStickySlashCommandFromMessage`` matches ``m.startsWith(cmd.prefix)``
   where ``cmd.prefix`` is ``"/cursor "``. ``"/cursor".startsWith("/cursor ")``
   is false → ``applyStickySlashAfterComposerSend`` calls ``clearAllSlashChips``
   and persists ``stickyCleared``.

Control commands (``/restart …``) must remain sendable with or without a
sticky agent chip.

Phase 2 slice 2C moved the composer decisions into ``chat_composer.js``
(``CuttleChatComposer.*``) and the slash decisions into ``chat_slash.js``;
this suite runs those real owner modules under Node (skipped if node is
absent) instead of slicing chat_page.js by name.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
SLASH_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_slash.js"
COMPOSER_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_composer.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract_inclusive(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head) + len(end_marker)
    return src[head:tail]


CURSOR_CHIP = {"prefix": "/cursor ", "label": "Cursor Agent", "category": "command"}
CODEX_CHIP = {"prefix": "/codex ", "label": "Codex", "category": "command"}
MODEL_CHIP = {
    "prefix": "/model auto",
    "label": "Auto",
    "category": "cursor-model",
}


def _run_js(script: str, *, chips=None) -> dict:
    """Run the composer/slash owner modules with the UI stubbed.

    The page adapters (compose over the active composer's resolved chips,
    send eligibility, sticky resolution after send) are expressed here as
    thin calls into the owner modules — the same delegation the page does.
    """
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    commands = _extract_inclusive(slash_src, "const SLASH_COMMANDS = [", "\n];")

    chips_json = json.dumps(chips if chips is not None else [CURSOR_CHIP])
    slash_mod = str(SLASH_JS)
    composer_mod = str(COMPOSER_JS)
    harness = f"""
const CuttleChatSlash = require({json.dumps(str(slash_mod))});
const CuttleChatComposer = require({json.dumps(str(composer_mod))});
{commands}
const CHIPS = {chips_json};

// chat_page.js adapters: resolve the active composer's chips from page
// state, then delegate the decision to the owner module.
function composeMessageWithSlashChip(textarea) {{
    return CuttleChatComposer.composeMessageWithSlashChip(
        {{ body: String((textarea && textarea.value) || '').trim(), chips: CHIPS }},
        {{ isControl: CuttleChatSlash.isNativeControlCommand }}
    );
}}
function isSendableComposerMessage(message, attachments) {{
    return CuttleChatComposer.isSendableComposerMessage(message, attachments, {{
        stickyOf: CuttleChatSlash.getStickySlashCommandFromMessage,
        isControl: CuttleChatSlash.isNativeControlCommand,
    }});
}}
function stickyCommandAfterSend(message) {{
    return CuttleChatComposer.stickyCommandAfterSend(
        {{ message }}, {{ stickyOf: CuttleChatSlash.getStickySlashCommandFromMessage }}
    );
}}
function getStickySlashCommandFromMessage(message) {{
    return CuttleChatSlash.getStickySlashCommandFromMessage(message);
}}
function isNativeControlCommand(text) {{
    return CuttleChatSlash.isNativeControlCommand(text);
}}

{script}
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------
# Bug 1 — blank send (agent chip + empty body must not submit)
# --------------------------------------------------------------------------


def test_compose_turns_empty_body_into_bare_agent_token():
    """Documents the compose shape that currently slips past the empty check."""
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({ composed }));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["composed"] == "/cursor"


def test_bare_sticky_agent_compose_is_not_sendable():
    """Enter with only /cursor (no text) must be treated as empty."""
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["composed"] == "/cursor"
    assert res["sendable"] is False


def test_bare_codex_chip_is_not_sendable():
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
""",
        chips=[CODEX_CHIP],
    )
    assert res["composed"] == "/codex"
    assert res["sendable"] is False


def test_cursor_plus_model_chip_alone_is_not_sendable():
    """Stacked /cursor + /model auto with no prompt body is still empty."""
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
""",
        chips=[CURSOR_CHIP, MODEL_CHIP],
    )
    assert res["composed"].startswith("/cursor")
    assert res["sendable"] is False


def test_real_prompt_with_sticky_chip_is_sendable():
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: 'fix the blank-send bug' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["composed"] == "/cursor fix the blank-send bug"
    assert res["sendable"] is True


def test_restart_alone_remains_sendable_with_sticky_chip():
    """Requirement 1: /restart must still work (control command)."""
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '/restart status' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
    isControl: isNativeControlCommand(composed),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["composed"] == "/restart status"
    assert res["isControl"] is True
    assert res["sendable"] is True


def test_restart_without_chip_is_sendable():
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '/restart graceful' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, []),
}));
""",
        chips=[],
    )
    assert res["composed"] == "/restart graceful"
    assert res["sendable"] is True


def test_attachment_only_is_sendable_even_with_bare_agent():
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '' });
process.stdout.write(JSON.stringify({
    composed,
    sendable: isSendableComposerMessage(composed, [{ filename: 'a.png' }]),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["sendable"] is True


def test_send_paths_must_gate_on_more_than_truthy_message():
    """Contract: welcome + chat send reject sticky-agent-only turns."""
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "function isSendableComposerMessage(message, attachments)" in src
    for fn_name in ("function sendMessage(", "function sendWelcomeMessage()"):
        assert fn_name in src
        start = src.index(fn_name)
        window = src[start : start + 3500]
        assert "isSendableComposerMessage(message, attachments)" in window, (
            f"{fn_name} must call isSendableComposerMessage so bare /cursor "
            "does not submit"
        )
        assert "if (!message && !attachments.length)" not in window, (
            f"{fn_name} still uses truthy-message empty check"
        )


# --------------------------------------------------------------------------
# Bug 2 — bare /cursor must keep the sticky chip pinned
# --------------------------------------------------------------------------


def test_bare_cursor_is_recognized_as_sticky_agent():
    """Root of the unpin: bare ``/cursor`` must match the stickySession command."""
    res = _run_js(
        """
const cmd = getStickySlashCommandFromMessage('/cursor');
process.stdout.write(JSON.stringify({
    found: !!cmd,
    prefix: cmd ? cmd.prefix : null,
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["found"] is True
    assert res["prefix"] == "/cursor "


def test_spaced_cursor_prompt_still_matches_sticky():
    res = _run_js(
        """
const cmd = getStickySlashCommandFromMessage('/cursor hello');
process.stdout.write(JSON.stringify({ found: !!cmd, prefix: cmd && cmd.prefix }));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["found"] is True
    assert res["prefix"] == "/cursor "


def test_apply_sticky_after_bare_cursor_keeps_chip():
    """Queue/send of compose('/cursor') must keep the sticky agent chip."""
    res = _run_js(
        """
const cmd = stickyCommandAfterSend('/cursor');
const sel = CuttleChatComposer.stickySessionSelection(CHIPS, {
    commands: SLASH_COMMANDS,
    clearedPending: false,
});
process.stdout.write(JSON.stringify({
    found: !!cmd,
    prefix: cmd ? cmd.prefix : null,
    stickyCleared: sel.stickyCleared,
    stickyPrefixes: sel.stickyChips.map((c) => c.prefix),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["found"] is True
    assert res["prefix"] == "/cursor "
    # Bare /cursor must not look like "user cleared the badge".
    assert res["stickyCleared"] is False
    assert res["stickyPrefixes"] == ["/cursor "]


def test_apply_sticky_after_queued_bare_cursor_keeps_chip():
    """Same path sendMessage uses when isSessionGenerating() queues a follow-up."""
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '' });
const cmd = stickyCommandAfterSend(composed);
const sel = CuttleChatComposer.stickySessionSelection(CHIPS, {
    commands: SLASH_COMMANDS,
    clearedPending: false,
});
process.stdout.write(JSON.stringify({
    composed,
    found: !!cmd,
    stickyCleared: sel.stickyCleared,
    stickyPrefixes: sel.stickyChips.map((c) => c.prefix),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["composed"] == "/cursor"
    assert res["found"] is True
    assert res["stickyCleared"] is False
    assert res["stickyPrefixes"] == ["/cursor "]


def test_apply_sticky_after_restart_keeps_agent_chip():
    """Control send must not evict the sticky agent (existing restart contract).

    chat_page.js keeps the agent chip on its control branch; the owner
    decisions are the control classification (true) and the absent sticky
    command (null), which together route to that branch.
    """
    res = _run_js(
        """
const isControl = isNativeControlCommand('/restart status');
const cmd = stickyCommandAfterSend('/restart status');
process.stdout.write(JSON.stringify({
    isControl,
    cmd: cmd ? cmd.prefix : null,
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["isControl"] is True
    assert res["cmd"] is None


def test_python_sticky_prefix_already_accepts_bare_cursor():
    """Server matcher is correct; the JS matcher must catch up."""
    from api.starred_slash import sticky_prefix_from_text

    assert sticky_prefix_from_text("/cursor") == "/cursor "
    assert sticky_prefix_from_text("/cursor ") == "/cursor "
    assert sticky_prefix_from_text("/cursor fix it") == "/cursor "
