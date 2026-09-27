"""Agent badge multi-tone segments: harness | model | optional effort."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


@node_only
def test_parse_agent_badge_segments():
    src = CHAT_JS.read_text(encoding="utf-8")
    helpers = _extract(
        src,
        "    /**\n     * Split an agent badge label into harness / model / effort",
        "    /**\n     * Inner HTML for a chip label: multi-tone segments when parseable",
    )
    escape = "function escapeHtmlInline(s) { return String(s); }\n"

    harness = f"""
{escape}
{helpers}
const cases = {{
  three: parseAgentBadgeSegments('Muse Code - muse-spark-1.2 · high'),
  twoCursor: parseAgentBadgeSegments('Cursor - Claude 4.6 Opus High Thinking'),
  effortOnly: parseAgentBadgeSegments('Hermes · ultra'),
  bare: parseAgentBadgeSegments('Cursor Agent'),
  empty: parseAgentBadgeSegments(''),
}};
process.stdout.write(JSON.stringify(cases));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["three"] == {
        "agent": "Muse Code",
        "model": "muse-spark-1.2",
        "effort": "high",
    }
    assert res["twoCursor"] == {
        "agent": "Cursor",
        "model": "Claude 4.6 Opus High Thinking",
        "effort": "",
    }
    assert res["effortOnly"] == {
        "agent": "Hermes",
        "model": "",
        "effort": "ultra",
    }
    assert res["bare"] is None
    assert res["empty"] is None


@node_only
def test_slash_chip_label_inner_html_is_segmented():
    src = CHAT_JS.read_text(encoding="utf-8")
    parse_helpers = _extract(
        src,
        "    /**\n     * Split an agent badge label into harness / model / effort",
        "    /** History/header chips with model·effort must not hyphen-chop",
    )
    escape = (
        "function escapeHtmlInline(s) {\n"
        "  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')"
        ".replace(/>/g,'&gt;').replace(/\"/g,'&quot;');\n}\n"
    )
    harness = f"""
{escape}
{parse_helpers}
const three = slashChipLabelInnerHtml('OpenCode - gpt-5.4 · medium');
const two = slashChipLabelInnerHtml('Cursor - Auto');
const bare = slashChipLabelInnerHtml('Cursor Agent');
process.stdout.write(JSON.stringify({{
  threeSeg: three.segmented,
  threeHasEffort: three.html.includes('slash-chip-seg--effort'),
  threeHasModel: three.html.includes('slash-chip-seg--model'),
  threeHasAgent: three.html.includes('slash-chip-seg--agent'),
  threeModelTitle: three.html.includes('title="gpt-5.4"'),
  threeEffortTitle: three.html.includes('title="medium"'),
  threeAgentTitle: three.html.includes('title="OpenCode"'),
  twoSeg: two.segmented,
  twoHasEffort: two.html.includes('slash-chip-seg--effort'),
  twoHasModel: two.html.includes('slash-chip-seg--model'),
  twoModelTitle: two.html.includes('title="Auto"'),
  bareSeg: bare.segmented,
  bareLabel: bare.html.includes('slash-chip-label'),
}}));
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    res = json.loads(proc.stdout)
    assert res["threeSeg"] is True
    assert res["threeHasEffort"] is True
    assert res["threeHasModel"] is True
    assert res["threeHasAgent"] is True
    assert res["threeModelTitle"] is True
    assert res["threeEffortTitle"] is True
    assert res["threeAgentTitle"] is True
    assert res["twoSeg"] is True
    assert res["twoHasEffort"] is False
    assert res["twoHasModel"] is True
    assert res["twoModelTitle"] is True
    assert res["bareSeg"] is False
    assert res["bareLabel"] is True


def test_frontend_wires_segmented_class():
    text = CHAT_JS.read_text(encoding="utf-8")
    assert "slash-command-chip--segmented" in text
    assert "slashChipLabelInnerHtml(displayLabel)" in text
    assert "parseAgentBadgeSegments" in text
    assert "Per-segment titles only" in text
    css = (REPO_ROOT / "src" / "web" / "css" / "chat_page.css").read_text(
        encoding="utf-8"
    )
    assert ".slash-command-chip--segmented" in css
    assert ".slash-chip-seg--model" in css
    assert "text-overflow: ellipsis" in css
