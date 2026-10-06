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

Runs real helpers from ``chat_page.js`` under Node (skipped if node is absent).
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


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


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
    """Stub UI; run real compose / sticky helpers from chat_page.js."""
    src = CHAT_JS.read_text(encoding="utf-8")
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    commands = _extract_inclusive(slash_src, "const SLASH_COMMANDS = [", "\n];")
    control = _extract(
        src,
        "    function isNativeControlCommand(text) {",
        "    const isControlCommandPrefix = isNativeControlCommand;",
    )
    compose = _extract_inclusive(
        src, "    function composeMessageWithSlashChip(textarea) {", "\n    }\n"
    )
    sendable = _extract_inclusive(
        src, "    function isSendableComposerMessage(message, attachments) {", "\n    }\n"
    )
    clear_chips = _extract_inclusive(
        src, "    function clearAllSlashChips() {", "\n    }\n"
    )
    # Stop before `let stickyAgentClearedPending` (declared in the harness).
    sticky_helpers = _extract(
        src,
        "    function applyStickySlashAfterComposerSend(message) {",
        "\n    let stickyAgentClearedPending = false;",
    )

    chips_json = json.dumps(chips if chips is not None else [CURSOR_CHIP])
    slash_mod = str(SLASH_JS)
    composer_mod = str(COMPOSER_JS)
    harness = f"""
const CuttleChatSlash = require("{slash_mod}");
const CuttleChatComposer = require("{composer_mod}");
{commands}
{control}
const isControlCommandPrefix = isNativeControlCommand;
const slashCtx = {{
    chat: {{ chips: {chips_json} }},
    welcome: {{ chips: {chips_json} }},
}};
let currentSessionId = 'CH-000450';
const prefsMap = {{}};
let stickyAgentClearedPending = false;
function slashContextKey() {{ return 'chat'; }}
function hideSlashMenu() {{}}
function renderSlashChips() {{}}
function getSessionPrefs(id) {{
    return prefsMap[String(id)] || null;
}}
function updateSessionPrefs(id, patch) {{
    prefsMap[String(id)] = Object.assign({{}}, prefsMap[String(id)] || {{}}, patch || {{}});
}}
function liveAgentBadgeLabelForChip(c) {{ return c && c.label; }}
function resolveSlashChipPaletteCategory(c) {{ return c && c.category; }}
const document = {{ getElementById: () => null }};
{compose}
{clear_chips}
{sticky_helpers}
{sendable}

{script}
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
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
    """Queue/send of compose('/cursor') must not clearAllSlashChips."""
    res = _run_js(
        """
slashCtx.chat.chips = [{ prefix: '/cursor ', label: 'Cursor Agent', category: 'command' }];
slashCtx.welcome.chips = slashCtx.chat.chips.slice();
applyStickySlashAfterComposerSend('/cursor');
process.stdout.write(JSON.stringify({
    chips: slashCtx.chat.chips.map((c) => c.prefix),
    prefs: prefsMap['CH-000450'] || null,
    stickyAgentClearedPending,
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["chips"] == ["/cursor "]
    # Bare /cursor must not look like "user cleared the badge".
    assert res["stickyAgentClearedPending"] is False
    if res["prefs"] is not None:
        assert res["prefs"].get("stickyCleared") is not True
        sticky = res["prefs"].get("stickyChips") or []
        if sticky:
            assert sticky[0]["prefix"] == "/cursor "


def test_apply_sticky_after_queued_bare_cursor_keeps_chip():
    """Same path sendMessage uses when isSessionGenerating() queues a follow-up."""
    res = _run_js(
        """
const composed = composeMessageWithSlashChip({ value: '' });
applyStickySlashAfterComposerSend(composed);
process.stdout.write(JSON.stringify({
    composed,
    chips: slashCtx.chat.chips.map((c) => c.prefix),
    stickyCleared: !!(prefsMap['CH-000450'] && prefsMap['CH-000450'].stickyCleared),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["composed"] == "/cursor"
    assert res["chips"] == ["/cursor "]
    assert res["stickyCleared"] is False


def test_apply_sticky_after_restart_keeps_agent_chip():
    """Control send must not evict the sticky agent (existing restart contract)."""
    res = _run_js(
        """
slashCtx.chat.chips = [{ prefix: '/cursor ', label: 'Cursor Agent', category: 'command' }];
slashCtx.welcome.chips = slashCtx.chat.chips.slice();
applyStickySlashAfterComposerSend('/restart status');
process.stdout.write(JSON.stringify({
    chips: slashCtx.chat.chips.map((c) => c.prefix),
}));
""",
        chips=[CURSOR_CHIP],
    )
    assert res["chips"] == ["/cursor "]


def test_python_sticky_prefix_already_accepts_bare_cursor():
    """Server matcher is correct; the JS matcher must catch up."""
    from api.starred_slash import sticky_prefix_from_text

    assert sticky_prefix_from_text("/cursor") == "/cursor "
    assert sticky_prefix_from_text("/cursor ") == "/cursor "
    assert sticky_prefix_from_text("/cursor fix it") == "/cursor "
