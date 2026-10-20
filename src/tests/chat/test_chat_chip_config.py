"""Chip config module pure helpers (chat_chip_config.js) under node.

The modal DOM/transport is a controller (composed by the page with a `host`);
these tests pin only the pure decision helpers that feed its dropdowns.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_chip_config.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};

// agentSettingOptions: strips (current) + the 'Effort ' prefix.
const spec = {
  models: { build: () => [
    { modelId: 'opus', label: 'Opus (current)' },
    { modelId: '', label: 'Loading…', disabled: true },
  ] },
  efforts: { build: () => [
    { modelId: 'high', label: 'Effort high (current)' },
    { modelId: 'low', label: 'Effort low' },
  ] },
};
out.modelOpts = A.agentSettingOptions(spec, 'models');
out.effortOpts = A.agentSettingOptions(spec, 'efforts');
out.missing = A.agentSettingOptions(spec, 'nope');
out.badBuild = A.agentSettingOptions({ models: { build: () => { throw new Error('x'); } } }, 'models');

// stickyAgentEntries: sticky rows only, de-duped by agent id, classifier-injected.
const commands = [
  { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor', stickySession: true },
  { prefix: '/codex ', label: 'Codex', category: 'codex', stickySession: true },
  { prefix: '/restart ', label: 'Restart', category: 'command', controlCommand: true },
];
const classify = (c) => (c.category === 'cursor' ? 'cursor'
  : c.category === 'codex' ? 'codex' : '');
out.entries = A.stickyAgentEntries(commands, classify);

// create() is a controller factory (DOM required); it must expose the API shape.
out.hasCreate = typeof A.create === 'function';
const ctrl = A.create({});
out.api = Object.keys(ctrl).sort();
out.branchEmpty = A.branchFor('no-such-path');

// repoChoice: Repository selector only for 2+ repos; remembered pick wins
// while still detected, else the first (server default) work tree.
const two = [
  { repo_root: '/p/A', label: 'A', branch: 'master' },
  { repo_root: '/p/B', label: 'B', branch: 'dev' },
];
out.repoOne = A.repoChoice([two[0]], '');
out.repoTwo = A.repoChoice(two, '/p/B');
out.repoStale = A.repoChoice(two, '/p/gone');
out.repoNone = A.repoChoice([], '');
// Chip label: the picked repo's branch, else the merged label.
out.labelPicked = A.activeRepoBranchLabel(two, '/p/B');
out.labelMerged = A.activeRepoBranchLabel(two, '');
out.labelSingle = A.activeRepoBranchLabel([two[1]], '/p/A');

process.stdout.write(JSON.stringify(out));
"""


def _run():
    return subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={"MOD_JS": str(MOD_JS), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )


@node_only
def test_helper_outputs():
    proc = _run()
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hasCreate"] is True
    assert out["api"] == ["bindRow", "close", "openAgent", "openProject"]
    assert out["branchEmpty"] == ""
    assert out["modelOpts"] == [
        {"value": "opus", "label": "Opus", "disabled": False},
    ]
    assert out["effortOpts"] == [
        {"value": "high", "label": "high", "disabled": False},
        {"value": "low", "label": "low", "disabled": False},
    ]
    assert out["missing"] == []
    assert out["badBuild"] == []
    assert [e["id"] for e in out["entries"]] == ["cursor", "codex"]
    assert out["entries"][0]["prefix"] == "/cursor "
    assert out["repoOne"]["show"] is False
    assert out["repoNone"] == {"show": False, "options": [], "selected": ""}
    assert out["repoTwo"]["show"] is True
    assert out["repoTwo"]["selected"] == "/p/B"
    assert [o["value"] for o in out["repoTwo"]["options"]] == ["/p/A", "/p/B"]
    assert out["repoTwo"]["options"][1]["label"] == "B  (dev)"
    assert out["repoStale"]["selected"] == "/p/A"
    assert out["labelPicked"] == "dev"
    assert out["labelMerged"] == "master +1"
    assert out["labelSingle"] == "dev"


@node_only
def test_module_loads_without_dom():
    """Requiring the module in node (no document) must not throw."""
    proc = subprocess.run(
        ["node", "-e", "require(process.env.MOD_JS); process.stdout.write('ok')"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={"MOD_JS": str(MOD_JS), "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "ok"
