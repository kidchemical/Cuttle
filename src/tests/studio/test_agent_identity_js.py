"""Agent identity helper (node): colour comes from the catalog, grey otherwise."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MODEL = REPO / "src" / "web" / "js" / "shared" / "agent_identity.js"

HARNESS = r"""
const M = require(process.env.MODEL);
const catalog = [
  { id: 'codex', label: 'Codex', color: '#19C37D' },
  { id: 'cursor', label: 'Cursor Agent', color: '#4c8dff' },
  { id: 'nocolor', label: 'No colour', color: '' },
  { id: 'badcolor', label: 'Bad <b>colour</b>', color: 'red; background:url(x)' },
  { id: 'named', label: 'Named "quote" & <tag>', color: '#a855f7' },
];
const out = {};
out.known = M.forAgent('codex', catalog);
out.lowerCased = M.forAgent('cursor', catalog).color;
out.unknown = M.forAgent('ghost', catalog);
out.unknownNoCatalog = M.forAgent('ghost', null);
out.emptyId = M.forAgent('', catalog);
out.missingColour = M.forAgent('nocolor', catalog);
out.badColour = M.forAgent('badcolor', catalog);
out.escapedName = M.forAgent('named', catalog).name;
out.dot = M.dotHtml(M.forAgent('codex', catalog));
out.dotForged = M.dotHtml({ color: 'red"></span><script>', name: 'x' });
out.label = M.labelHtml(M.forAgent('named', catalog));
out.labelUnknown = M.labelHtml(M.forAgent('ghost', catalog));
out.labelNoIdentity = M.labelHtml(null);
out.neutral = M.NEUTRAL_COLOR;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result():
    if not shutil.which("node"):
        pytest.skip("node not available")
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        env={**os.environ, "MODEL": str(MODEL)},
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_known_agent_takes_colour_and_name_from_catalog(result):
    assert result["known"] == {"color": "#19c37d", "name": "Codex"}
    assert result["lowerCased"] == "#4c8dff"


def test_unknown_agent_is_neutral_grey(result):
    assert result["unknown"] == {"color": result["neutral"], "name": "ghost"}
    assert result["unknownNoCatalog"] == {"color": result["neutral"], "name": "ghost"}
    assert result["emptyId"] == {"color": result["neutral"], "name": "Agent"}


def test_missing_or_malformed_colour_is_neutral(result):
    assert result["missingColour"]["color"] == result["neutral"]
    assert result["badColour"]["color"] == result["neutral"]
    assert result["badColour"]["name"] == "Bad <b>colour</b>"


def test_neutral_grey_is_a_hex_value(result):
    assert result["neutral"] == "#6b7280"


def test_dot_markup_is_decorative_and_uses_only_a_validated_colour(result):
    assert result["dot"] == '<span class="agent-dot" style="background:#19c37d" aria-hidden="true"></span>'
    assert "script" not in result["dotForged"]
    assert "background:#6b7280" in result["dotForged"]


def test_label_escapes_the_name(result):
    assert result["label"].endswith('<span class="agent-identity-name">Named &quot;quote&quot; &amp; &lt;tag&gt;</span></span>')
    assert result["label"].startswith('<span class="agent-identity"><span class="agent-dot" style="background:#a855f7"')


def test_label_without_identity_still_renders(result):
    assert 'class="agent-identity-name">Agent<' in result["labelNoIdentity"]
    assert "background:#6b7280" in result["labelNoIdentity"]
