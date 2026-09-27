"""Behavioural test for the chat composer: a sticky agent chip must not turn
`/restart …` into an agent prompt.

Runs the real `composeMessageWithSlashChip` / `isNativeControlCommand` source
out of chat_page.js under Node, with the surrounding UI stubbed. Skipped when
Node is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head) + len(end_marker)
    return src[head:tail]


def _run_composer(chips, body):
    src = CHAT_JS.read_text(encoding="utf-8")
    commands = _extract(src, "const SLASH_COMMANDS = [", "\n    ];")
    control = _extract(
        src,
        "    function isNativeControlCommand(text) {",
        "const isControlCommandPrefix = isNativeControlCommand;",
    )
    compose = _extract(
        src, "    function composeMessageWithSlashChip(textarea) {", "\n    }\n"
    )

    harness = f"""
{commands}
{control}
const slashCtx = {{
    chat: {{ chips: {json.dumps(chips)} }},
    welcome: {{ chips: [] }},
}};
function slashContextKey() {{ return 'chat'; }}
{compose}
const out = composeMessageWithSlashChip({{ value: {json.dumps(body)} }});
process.stdout.write(JSON.stringify({{ out: out }}));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["out"]


CURSOR_CHIP = {"prefix": "/cursor ", "label": "Cursor Agent", "category": "command"}
CODEX_CHIP = {"prefix": "/codex ", "label": "Codex", "category": "command"}
RESTART_CHIP = {"prefix": "/restart status", "label": "Restart status", "category": "command"}


def test_typed_control_command_ignores_active_cursor_chip():
    assert _run_composer([CURSOR_CHIP], "/restart status") == "/restart status"


def test_typed_control_command_ignores_starred_codex_chip():
    assert _run_composer([CODEX_CHIP], "/restart graceful") == "/restart graceful"


def test_control_chip_outranks_stacked_sticky_chip():
    assert _run_composer([CURSOR_CHIP, RESTART_CHIP], "") == "/restart status"


def test_normal_prompts_still_get_the_sticky_prefix():
    assert _run_composer([CURSOR_CHIP], "fix the bug") == "/cursor fix the bug"


def test_other_slash_commands_still_get_the_sticky_prefix():
    assert _run_composer([CURSOR_CHIP], "/model auto") == "/cursor /model auto"
