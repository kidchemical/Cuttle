"""Generic badge-gated harness pins (chat_agent_controls.js HARNESS_PIN_SPECS).

Antigravity rides the shared controller instead of a sixth hand-copied
model/effort palette: rows, picks → ``/api/antigravity/model|effort``, seeding
from ``agent_pins``, badge label, request pins and the assistant badge must all
work through it, and Claude's wrappers over the shared helpers keep their output.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

SCRIPT = r"""
const path = require('path');
const dir = process.env.CHAT_JS;
require(path.join(dir, 'chat_slash.js'));
require(path.join(dir, 'chat_agent_model.js'));
const C = require(path.join(dir, 'chat_agent_controls.js'));
const state = C.createProviderState();
const posts = [];
let chip = true;
const catalog = { success: true, preferredModel: '', source: 'cli', count: 2, commonEfforts: ['low', 'high', 'xhigh'],
  models: [{ id: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash', efforts: ['low', 'high'] },
           { id: 'gemini-pro-orchestrator-neon', label: 'Orchestrator (Neon)', efforts: [] }] };
const respond = (url, opts) => {
  if (opts && opts.method === 'POST') { posts.push([url, JSON.parse(opts.body)]); return { success: true }; }
  if (url.startsWith('/api/antigravity/models')) return catalog;
  if (url.startsWith('/api/antigravity/effort')) return { success: true, preferredEffort: '' };
  return { success: false };
};
const ctl = C.create({
  state, getSessionId: () => '77',
  hasActiveHarness: (id) => chip && id === 'antigravity',
  fetch: async (url, opts) => ({ json: async () => respond(url, opts) }),
});
const tick = () => new Promise((r) => setTimeout(r, 0));
(async () => {
  const out = {};
  const h = ctl.harnessPins('antigravity');
  out.ids = ctl.harnessPinIds();
  out.claudeIsHandWritten = ctl.harnessPins('claude') === null;
  ctl.buildHarnessPinPaletteItems('antigravity model');
  await tick(); await tick();
  const rows = ctl.buildHarnessPinPaletteItems('antigravity model');
  out.rowPrefixes = rows.map((r) => r.prefix);
  out.rowFlags = rows.filter((r) => r.category === 'antigravity-model').map((r) => !!r.antigravityModel);
  out.handled = ctl.handleHarnessPinPick(rows.find((r) => r.modelId === 'gemini-3.8-flash'));
  await tick(); await tick();
  const efforts = ctl.buildHarnessPinPaletteItems('antigravity effort');
  out.effortIds = efforts.filter((r) => r.category === 'antigravity-effort' && !r.disabled).map((r) => r.modelId);
  ctl.handleHarnessPinPick(efforts.find((r) => r.modelId === 'high'));
  await tick(); await tick();
  out.posts = posts;
  out.badge = h.badgeLabel();
  out.tooltip = h.tooltip();
  const body = { message: '/antigravity hello' };
  ctl.attachAgentIdentityToRequest(body);
  out.requestPins = body.agent_pins;
  out.reply = ctl.enrichSlashCommandWithAgentBadge(
    { chips: [{ label: 'Antigravity', meta: '/antigravity', category: 'antigravity' }] },
    { agent_id: 'antigravity', agent_model: 'gemini-3.8-flash', agent_effort: 'high' });
  ctl.seedHarnessPinsFromSessionData({ agent_pins: { antigravity: { model: 'gemini-4-neon', effort: 'xhigh' } } }, '78');
  out.seeded = [state.antigravityModel, state.antigravityEffort];
  out.menu = Object.keys(h.menuSpec());
  out.bare = [h.isBareLabel('Antigravity CLI'), h.isBareLabel('Antigravity - Gemini 4 Neon')];
  chip = false;
  out.noChipRows = ctl.buildHarnessPinPaletteItems('antigravity model').length;
  ctl.resetAgentSupplementForIdentity('77', '78');
  out.afterReset = [state.antigravityModel, state.antigravityModels.length];
  console.log(JSON.stringify(out));
})();
"""


def _run():
    proc = subprocess.run(["node", "-e", SCRIPT], capture_output=True, text=True, encoding="utf-8",
                          env={"CHAT_JS": str(CHAT_JS), "PATH": __import__("os").environ.get("PATH", "")})
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_antigravity_rides_the_generic_harness_pin_controller():
    res = _run()
    assert res["ids"] == ["antigravity"] and res["claudeIsHandWritten"] is True
    assert res["rowPrefixes"][0] == "/antigravity model refresh"
    assert "/antigravity model gemini-3.8-flash" in res["rowPrefixes"]
    assert all(res["rowFlags"])
    assert res["handled"] is True
    # Effort rows come from the picked model's own levels, not the union.
    assert res["effortIds"] == ["low", "high"]
    assert res["posts"] == [
        ["/api/antigravity/model", {"session": "77", "model": "gemini-3.8-flash"}],
        ["/api/antigravity/effort", {"session": "77", "effort": "high", "model": "gemini-3.8-flash"}],
    ]
    assert res["badge"].startswith("Antigravity") and "Gemini 3.8 Flash" in res["badge"] and "high" in res["badge"]
    assert "/antigravity model … to change" in res["tooltip"]
    assert res["requestPins"] == {"antigravity": {"model": "gemini-3.8-flash", "effort": "high"}}
    reply_label = res["reply"]["chips"][0]["label"]
    assert "Gemini 3.8 Flash" in reply_label and "high" in reply_label
    assert res["seeded"] == ["gemini-4-neon", "xhigh"]
    assert res["menu"] == ["models", "efforts"]
    assert res["bare"] == [True, False]
    assert res["noChipRows"] == 0  # badge-gated like Claude
    assert res["afterReset"] == ["", 0]
