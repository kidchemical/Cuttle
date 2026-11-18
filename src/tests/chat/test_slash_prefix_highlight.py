"""Studio slash palette: the typed prefix is bolded, escape-safe (node unit test).

`highlightTypedPrefixHtml` is a pure function exported by chat_slash_palette.js.
The query is matched as literal characters: regex metacharacters are plain text,
and HTML-special input comes back escaped, never as markup.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PALETTE_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_slash_palette.js"

node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

_RUNNER = """
const mod = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([text, query]) => mod.highlightTypedPrefixHtml(text, query));
console.log(JSON.stringify(out));
"""

OPEN = '<strong class="slash-prefix-hit">'


def _highlight(cases):
    proc = subprocess.run(
        ["node", "-e", _RUNNER, str(PALETTE_JS), json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(proc.stdout)


@node_only
def test_prefix_is_bolded_including_leading_slash():
    assert _highlight([["/cursor", "cu"]]) == [OPEN + "/cu</strong>rsor"]


@node_only
def test_match_is_case_insensitive_and_keeps_entry_case():
    assert _highlight([["/Orchestrate", "ORCH"]]) == [OPEN + "/Orch</strong>estrate"]


@node_only
def test_entry_without_leading_slash_is_bolded_from_its_start():
    assert _highlight([["Cursor", "cu"]]) == [OPEN + "Cu</strong>rsor"]


@node_only
def test_spaces_inside_the_query_match_the_entry_text():
    assert _highlight([["/model sonnet", "model son"]]) == [OPEN + "/model son</strong>net"]


@node_only
def test_empty_or_non_prefix_query_returns_escaped_text_only():
    assert _highlight([["/cursor", ""], ["/cursor", "x"], ["/cursor", "rsor"]]) == [
        "/cursor",
        "/cursor",
        "/cursor",
    ]


@node_only
def test_query_longer_than_entry_bolds_the_whole_entry():
    assert _highlight([["/cursor", "cursor-x"]]) == [OPEN + "/cursor</strong>"]


@node_only
def test_regex_metacharacters_in_query_are_literal():
    # `.*` must not act as a wildcard; `x(` must match the literal text.
    assert _highlight([["/a.*b", ".*"], ["/a.*b", "a.*"]]) == ["/a.*b", OPEN + "/a.*</strong>b"]
    assert _highlight([["/x(y)", "x("]]) == [OPEN + "/x(</strong>y)"]


@node_only
def test_html_special_characters_are_escaped_in_text_and_query():
    out = _highlight([["/a<b>&\"'", "a<b>"]])[0]
    assert out == OPEN + "/a&lt;b&gt;</strong>&amp;&quot;&#39;"


@node_only
def test_html_in_query_never_becomes_markup():
    out = _highlight([["/<img src=x onerror=alert(1)>", "<img"]])[0]
    assert "<img" not in out
    assert out == OPEN + "/&lt;img</strong> src=x onerror=alert(1)&gt;"
    assert _highlight([["/cursor", "<script>"]]) == ["/cursor"]


@node_only
def test_stripping_tags_gives_back_the_escaped_entry():
    cases = [
        ["/cursor", "cu"],
        ["/a<b>&\"'", "a<b"],
        ["/x(y)", "x("],
        ["/model sonnet", "model son"],
    ]
    for (text, _query), html in zip(cases, _highlight(cases)):
        stripped = re.sub(r"</?strong[^>]*>", "", html)
        expected = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        expected = expected.replace('"', "&quot;").replace("'", "&#39;")
        assert stripped == expected


def test_palette_exports_the_highlight_and_gates_it_on_studio():
    """Source-level guard: the bold only renders when the Studio check says so."""
    src = PALETTE_JS.read_text(encoding="utf-8")
    assert "const studioPrefix = isStudio() ? String(state.lastFilter || '') : null;" in src
    assert "studioPrefix != null" in src
    assert "highlightTypedPrefixHtml, " in src or "highlightTypedPrefixHtml }" in src
