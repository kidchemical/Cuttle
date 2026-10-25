"""Spaces state model + persistence: sanitize/load/save/transitions.

Behavioral characterization of src/web/js/spaces/spaces_state.js under node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_JS = REPO_ROOT / "src" / "web" / "js" / "spaces" / "spaces_state.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const S = require(process.env.STATE_JS);
const store = (data) => ({ getItem: () => data, setItem: () => {} });
const dirty = {
  active: 's2', spaces: [
    { id: 's1', name: 'A', root: null },
    { id: 's2', name: 'B', root: { type: 'leaf' }, groupId: 'g1', color: '#8ab4f8',
      projectOverride: { id: 7, name: 'Demo', path: '/p/demo' },
      agentOverride: { prefix: '/cursor ', label: 'Cursor Agent', category: 'cursor', id: 'cursor' } },
    { id: 's3', name: 'C', groupId: 'nope', color: 'not-a-color',
      projectOverride: {}, agentOverride: { label: 'missing prefix' } },
    { id: null },
  ],
  groups: [
    { id: 'g1', name: 'Long name here', color: '#8ab4f8', collapsed: 1,
      projectOverride: { id: 4, name: 'Cuttle' }, agentOverride: { prefix: '/nope' } },
    { id: 'g1', name: 'dupe' },
    null,
  ],
};
const clean = S.sanitizeLoadedData(dirty);
const roundTrip = (() => {
  let held = null;
  const mem = { getItem: () => held, setItem: (k, v) => { held = v; } };
  S.saveSpacesState(mem, clean);
  return S.loadSpacesState(mem);
})();
const bad = S.sanitizeLoadedData({ spaces: [] });
const fallbackActive = S.sanitizeLoadedData({ active: 'ghost', spaces: [{ id: 's1', name: 'A', root: null }], groups: [] });
const st = { active: 's1', spaces: [{ id: 's1', name: 'Space 1', root: null }], groups: [] };
const added = S.addSpaceToState(st);
const addedName = added.name;
const plan1 = S.planSpaceClose({ active: 's1', spaces: [{ id: 's1' }] }, 's1');
const plan2 = S.planSpaceClose({ active: 's1', spaces: [{ id: 's1' }, { id: 's2' }] }, 's1');
const renamed = S.renameSpaceInState(st, added.id, '  Team  ');
const renamedEmpty = S.renameSpaceInState(st, added.id, '   ');
const inherit = S.resolveNewChatDefaults({
  active: 's2',
  spaces: [
    { id: 's2', groupId: 'g1', projectOverride: { id: 7, name: 'Demo', path: '/p/demo' } },
  ],
  groups: [{ id: 'g1', agentOverride: { prefix: '/codex ', label: 'Codex', id: 'codex' } }],
});
const inheritGroup = S.resolveNewChatDefaults({
  active: 's1',
  spaces: [{ id: 's1', groupId: 'g1' }],
  groups: [{ id: 'g1', projectOverride: { id: 4, name: 'Cuttle' },
    agentOverride: { prefix: '/muse ', label: 'Muse Code', id: 'muse' } }],
});
process.stdout.write(JSON.stringify({ clean, roundTrip, bad, fallbackActive, added, addedName, plan1, plan2, renamed, renamedEmpty,
  names: S.nextSpaceName([{ name: 'Space 1' }, { name: 'space 2' }]),
  colorOk: S.sanitizeColor('#8AB4F8'), colorBad: S.sanitizeColor('red'),
  inherit, inheritGroup,
  projOk: S.sanitizeProjectOverride({ id: 7, name: 'Demo', path: '/p' }),
  projBad: S.sanitizeProjectOverride({}),
  agentOk: S.sanitizeAgentOverride({ prefix: '/cursor', id: 'cursor' }),
  agentBad: S.sanitizeAgentOverride({ label: 'x' }) }));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "STATE_JS": str(STATE_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_sanitize_keeps_valid_drops_invalid():
    res = _run()["clean"]
    assert [s["id"] for s in res["spaces"]] == ["s1", "s2", "s3"]
    assert res["spaces"][1]["groupId"] == "g1"
    assert res["spaces"][1]["color"] == "#8ab4f8"
    assert res["spaces"][1]["projectOverride"]["id"] == 7
    assert res["spaces"][1]["agentOverride"]["prefix"] == "/cursor "
    assert "groupId" not in res["spaces"][2]  # unknown group dropped
    assert "color" not in res["spaces"][2]  # arbitrary color dropped
    assert "projectOverride" not in res["spaces"][2]
    assert "agentOverride" not in res["spaces"][2]
    assert len(res["groups"]) == 1  # dupe + null removed
    assert res["groups"][0]["collapsed"] is True
    assert res["groups"][0]["projectOverride"]["id"] == 4
    assert res["groups"][0]["agentOverride"]["prefix"] == "/nope "
    assert res["active"] == "s2"


@node_only
def test_new_chat_defaults_inherit_space_then_group():
    res = _run()
    assert res["inherit"]["project"]["id"] == 7
    assert res["inherit"]["agent"]["id"] == "codex"
    assert res["inheritGroup"]["project"]["id"] == 4
    assert res["inheritGroup"]["agent"]["id"] == "muse"
    assert res["projOk"] == {"id": 7, "name": "Demo", "path": "/p"}
    assert res["projBad"] is None
    assert res["agentOk"]["prefix"] == "/cursor "
    assert res["agentOk"]["id"] == "cursor"
    assert res["agentBad"] is None


@node_only
def test_sanitize_rejects_empty_and_falls_back_active():
    res = _run()
    assert res["bad"] is None
    assert res["fallbackActive"]["active"] == "s1"


@node_only
def test_save_load_round_trip():
    res = _run()
    assert res["roundTrip"] == res["clean"]


@node_only
def test_space_lifecycle_helpers():
    res = _run()
    assert res["addedName"] == "Space 2"
    assert res["plan1"] == {"ok": False, "switchTo": None}  # last space cannot close
    assert res["plan2"] == {"ok": True, "switchTo": "s2"}  # active close switches to neighbor
    assert res["renamed"] is True
    assert res["renamedEmpty"] is False
    assert res["names"] == "Space 3"  # case-insensitive collision avoidance
    assert res["colorOk"] == "#8ab4f8"
    assert res["colorBad"] is None


@node_only
def test_palette_matches_legacy_shell_palette():
    """Exact preset list (a wrong hex silently clears saved colors)."""
    import subprocess as sp, os
    proc = sp.run(
        ["node", "-e", "const S=require(process.env.STATE_JS);process.stdout.write(JSON.stringify(S.COLORS))"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "STATE_JS": str(STATE_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [
        {"name": "White", "hex": "#e8eaed"},
        {"name": "Grey", "hex": "#9aa0a6"},
        {"name": "Blue", "hex": "#8ab4f8"},
        {"name": "Red", "hex": "#f28b82"},
        {"name": "Yellow", "hex": "#fdd663"},
        {"name": "Green", "hex": "#81c995"},
        {"name": "Pink", "hex": "#ff8bcb"},
        {"name": "Purple", "hex": "#c58af9"},
        {"name": "Cyan", "hex": "#78d9ec"},
        {"name": "Orange", "hex": "#fcad70"},
        {"name": "Teal", "hex": "#469990"},
        {"name": "Magenta", "hex": "#f032e6"},
        {"name": "Lime", "hex": "#bfef45"},
        {"name": "Brown", "hex": "#9a6324"},
        {"name": "Navy", "hex": "#000075"},
    ]


@node_only
def test_new_swatches_sanitize_and_persist():
    """The 5 added presets must survive sanitize (old code dropped them)."""
    import subprocess as sp, os
    proc = sp.run(
        ["node", "-e",
         "const S=require(process.env.STATE_JS);"
         "const hexes=['#469990','#f032e6','#bfef45','#9a6324','#000075'];"
         "const out=hexes.map((h)=>S.sanitizeColor(h.toUpperCase()));"
         "process.stdout.write(JSON.stringify(out))"],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "STATE_JS": str(STATE_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [
        "#469990", "#f032e6", "#bfef45", "#9a6324", "#000075",
    ]


@node_only
def test_state_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(STATE_JS)], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
