"""Behavioral coverage for src/web/js/chat_markdown.js under node.

Executes the real module (never structural pins): inline spans, table
planning/rendering, and the block line loop over representative and
adversarial inputs. The pre-move differential (/tmp/diff_markdown.js,
scratch) showed byte-identical outputs vs the page spans these were
extracted from; the full-pipeline test in test_chat_messages.py covers
the same code through the real formatMessage.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

MOD_MD = REPO_ROOT / "src" / "web" / "js" / "chat_markdown.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
(async () => {
const M = require(process.env.MOD_MD);
const out = {};
out.inline = {
  code: M.formatInlineMarkdown('use `x` here'),
  bold: M.formatInlineMarkdown('a **b** c'),
  emStar: M.formatInlineMarkdown('a *b* c'),
  emUnderscore: M.formatInlineMarkdown('a _b_ c'),
  snake: M.formatInlineMarkdown('snake_case stays'),
  mixed: M.formatInlineMarkdown('`a` **b _c_ d** e'),
  empty: M.formatInlineMarkdown(''),
};
out.helpers = {
  split: M.splitMarkdownTableRow('  |a|b|  '),
  single: M.splitMarkdownTableRow('| only |'),
  sepOk: M.isMarkdownTableSeparatorRow('| --- | ---: |'),
  sepShort: M.isMarkdownTableSeparatorRow('| -- | x |'),
  sepSingle: M.isMarkdownTableSeparatorRow('| --- |'),
  looksPipe: M.looksLikeMarkdownTableRow('a | b'),
  looksNone: M.looksLikeMarkdownTableRow('no pipe'),
  alignC: M.markdownTableAlignFromSep(':---:'),
  alignR: M.markdownTableAlignFromSep('---:'),
  alignL: M.markdownTableAlignFromSep(':---'),
  alignNone: M.markdownTableAlignFromSep('---'),
};
out.table = M.renderMarkdownTableHtml(['*H*', 'S'], ['', 'right'],
  [['a', 'b'], ['c']]);
out.tableRagged = M.renderMarkdownTableHtml(['A', 'B', 'C'],
  ['left', 'center', 'right'], [['1', '2', '3', 'EXTRA']]);
// Production escapes (<>&) before the loop; the harness does the same so
// quote/callout routing sees &gt; exactly as the pipeline feeds it.
const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const blocks = (t) => M.renderMarkdownBlocks(esc(t));
out.blocks = {
  header: blocks('# Title *b*'),
  headerNoSpace: blocks('#H no space'),
  list: blocks('- a\\n- b'),
  ordered: blocks('1. one\\n2. two'),
  listThenPara: blocks('- a\\n\\ntext'),
  table: blocks('| A | B |\\n| --- | ---: |\\n| 1 | 2 |'),
  tableNoSep: blocks('| A | B |\\nnot sep\\n| C | D |'),
  tableRaggedRow: blocks('| A | B |\\n| --- | --- |\\n| only'),
  quote: blocks('> quoted **bold**\\n> more'),
  callout: blocks('> [!WARNING] careful'),
  calloutNote: blocks('> [!NOTE] fine'),
  quoteSplit: blocks('> q1\\n\\n> q2'),
  rule: blocks('para\\n\\n---\\n\\nmore'),
  empties: blocks('a\\n\\n\\n\\nb'),
  codeChip: blocks('{{CUTTLE_CODE_0}}'),
  formChip: blocks('x\\n{{CUTTLE_FORM_2}}\\ny'),
  fakeChip: blocks('{{CUTTLE_FAKE_9}}'),
  linkInPara: blocks('para {{CUTTLE_LINK_3}} end'),
  mixedList: blocks('1. one\\n2. two\\n- mix\\n3. three'),
  headAfterList: blocks('- item\\n# head after list'),
  fenceLiteral: blocks('```not fence```'),
};
process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error('HARNESS-ERROR', e); process.exit(2); });
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_MD": str(MOD_MD)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_inline_spans_and_underscore_edges():
    res = _run()["inline"]
    assert res["code"] == "use <code>x</code> here"
    assert res["bold"] == "a <strong>b</strong> c"
    assert res["emStar"] == "a <em>b</em> c"
    assert res["emUnderscore"] == "a <em>b</em> c"
    assert res["snake"] == "snake_case stays"
    assert res["mixed"] == "<code>a</code> <strong>b <em>c</em> d</strong> e"
    assert res["empty"] == ""


@node_only
def test_table_planning_helpers():
    res = _run()["helpers"]
    assert res["split"] == ["a", "b"]
    assert res["single"] == ["only"]
    assert res["sepOk"] is True
    assert res["sepShort"] is False
    assert res["sepSingle"] is False
    assert res["looksPipe"] is True
    assert res["looksNone"] is False
    assert (res["alignC"], res["alignR"], res["alignL"],
            res["alignNone"]) == ("center", "right", "left", "")


@node_only
def test_table_render_alignment_and_ragged_rows():
    res = _run()
    assert "<th><em>H</em></th>" in res["table"]
    assert '<th style="text-align:right">S</th>' in res["table"]
    assert '<td>c</td><td style="text-align:right"></td>' in res["table"]
    ragged = res["tableRagged"]
    # An over-wide body row widens the column count; the extra cell is kept.
    assert "<td>EXTRA</td>" in ragged
    assert 'style="text-align:left"' in ragged
    assert 'style="text-align:center"' in ragged


@node_only
def test_block_loop_structures():
    b = _run()["blocks"]
    assert b["header"] == '<h1 class="message-header">Title <em>b</em></h1>'
    assert b["headerNoSpace"] == "<p>#H no space</p>"
    assert b["list"] == "<ul><li>a</li><li>b</li></ul>"
    assert b["ordered"] == "<ol><li>one</li><li>two</li></ol>"
    assert b["listThenPara"] == "<ul><li>a</li></ul><p>text</p>"
    assert b["mixedList"].count("<li>") == 4
    assert b["headAfterList"] == (
        "<ul><li>item</li></ul>"
        '<h1 class="message-header">head after list</h1>')
    assert b["quote"].startswith('<blockquote class="message-blockquote">')
    assert "<strong>bold</strong>" in b["quote"] and "<br>" in b["quote"]
    assert "message-blockquote--warning" in b["callout"]
    assert "Warning" in b["callout"]
    assert "message-blockquote--warning" not in b["calloutNote"]
    assert b["quoteSplit"].count("<blockquote") == 2
    # An empty line after a paragraph emits one <br>, never before a rule.
    assert b["rule"] == "<p>para</p><br><hr class=\"message-hr\"><p>more</p>"
    assert b["empties"] == "<p>a</p><br><p>b</p>"
    # Inline-code pairing wins over fence intuition on a single line.
    assert b["fenceLiteral"] == "<p>``<code>not fence</code>``</p>"


@node_only
def test_block_loop_tables_and_placeholders():
    b = _run()["blocks"]
    assert '<div class="message-table-wrap">' in b["table"]
    assert b["tableNoSep"] == (
        "<p>| A | B |</p><p>not sep</p><p>| C | D |</p>")
    # A non-row line ends the table; the stray line becomes a paragraph.
    assert "<tbody></tbody>" in b["tableRaggedRow"]
    assert b["tableRaggedRow"].endswith("<p>| only</p>")
    # Bare block placeholders pass through for the restore passes.
    assert b["codeChip"] == "{{CUTTLE_CODE_0}}"
    assert b["formChip"] == "<p>x</p>{{CUTTLE_FORM_2}}<p>y</p>"
    assert b["fakeChip"] == "<p>{{CUTTLE_FAKE_9}}</p>"
    assert b["linkInPara"] == "<p>para {{CUTTLE_LINK_3}} end</p>"


@node_only
def test_chat_markdown_module_parses():
    import os
    proc = subprocess.run(
        ["node", "--check", str(MOD_MD)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stderr


def test_chat_markdown_script_tag_versioned_and_ordered():
    html = (REPO_ROOT / "src" / "web" / "chat_page.html").read_text(encoding="utf-8")
    tag = '<script src="/js/chat_markdown.js?v='
    assert tag in html, "chat_markdown.js must load via versioned script tag"
    assert html.index("chat_messages.js") < html.index("chat_markdown.js") < html.index(
        "chat_page.js?v="
    ), "load order: owned modules before the page orchestrator"
