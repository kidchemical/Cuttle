"""Composer chip-group removal (CH-000419 follow-up) — no LLM turns.

Removing a merged agent badge (e.g. ``/cursor`` + ``/model <id>``) must delete
the whole agent group so no residual subcommand chip is left behind.
Exercises the real pure helpers from chat_page.js under Node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

CHAT_JS = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"

_HELPERS = ("composerChipAgentId", "composerChipRemovalIndexes")


def _extract_function(src: str, name: str) -> str:
    """Slice one top-level ``function name(...) {...}`` by brace matching."""
    start = src.index(f"    function {name}(")
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces in {name}")


_DRIVER_TMPL = """%s
const assert = require('assert');
const cases = %s;
for (const [chips, idx, want] of cases) {
  assert.deepStrictEqual(composerChipRemovalIndexes(chips, idx), want,
    'idx=' + idx + ' chips=' + JSON.stringify(chips));
}
console.log('chip-removal-ok');
"""


@node_only
def test_merged_agent_badge_removes_as_a_whole(tmp_path):
    src = CHAT_JS.read_text(encoding="utf-8")
    helpers = "\n".join(_extract_function(src, name) for name in _HELPERS)
    cursor_pair = [
        {"prefix": "/cursor ", "category": "cursor", "label": "Cursor"},
        {"prefix": "/model xyz", "category": "cursor-model", "modelId": "xyz", "label": "X"},
    ]
    cases = [
        # Either chip of a merged Cursor badge removes the whole group.
        (cursor_pair, 0, [0, 1]),
        (cursor_pair, 1, [0, 1]),
        # Unrelated chips are untouched by an agent removal.
        (cursor_pair + [{"prefix": "/cmd deploy", "category": "project-cmd", "label": "deploy"}], 0, [0, 1]),
        # A lone agent chip removes just itself.
        ([{"prefix": "/muse ", "category": "command", "label": "Muse Code"}], 0, [0]),
        # Agent-less chips remove singly.
        (
            [
                {"prefix": "/muse ", "category": "command", "label": "Muse Code"},
                {"prefix": "/cmd deploy", "category": "project-cmd", "label": "deploy"},
            ],
            1,
            [1],
        ),
        # Degenerate inputs never throw.
        ([], 0, []),
        (None, 0, []),
        (cursor_pair, 9, []),
    ]
    driver = tmp_path / "chip-removal.js"
    driver.write_text(_DRIVER_TMPL % (helpers, json.dumps(cases)), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(driver)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    assert "chip-removal-ok" in proc.stdout
