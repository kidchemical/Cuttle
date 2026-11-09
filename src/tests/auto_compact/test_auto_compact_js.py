"""Auto-compact client owners under node: gauge-popover section (chat_auto_compact.js),
Settings form patch (settings_auto_compact.js) and the gauge's section slot."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

HARNESS = r"""
const A = require(process.env.CHAT_JS);
const S = require(process.env.SETTINGS_JS);
const G = require(process.env.GAUGE_JS);
const out = {};
const view = {
  available: true, managed: true, managed_reason: 'supervised',
  override: { percent: 55 },
  policy: { enabled: true, percent: 55, max_tokens: 250000, milestone: true, milestone_percent: 40 },
  inherited: { enabled: true, percent: 60, milestone: true },
  state: { last_decision: 'compacted' },
  history: [{ outcome: 'override' }, { outcome: 'compacted', detail: { before: 82, after: 14, trigger: 'milestone' } }],
};
out.model = A.sectionModel(view);
out.hidden = A.sectionModel({ available: false });
out.html = A.renderSectionHtml(view, (s) => String(s).replace(/</g, '&lt;'));
out.offHtml = A.renderSectionHtml({ available: false });
out.off = A.sectionModel({ ...view, override: { enabled: false }, policy: { ...view.policy, enabled: false } }).rule;
out.failing = A.sectionModel({ ...view, state: { last_decision: 'failing' } }).failing;
out.override = [
  A.overrideFrom({ enabled: 'inherit', milestone: 'off', percent: '' }),
  A.overrideFrom({ enabled: 'on', milestone: 'inherit', percent: ' 65 ' }),
];
out.patch = S.patchFrom({
  chats: { enabled: true, percent: '70', max_k: '300' },
  managed: { enabled: false, percent: '55', max_k: '0' },
  milestone: { enabled: true, percent: '40', max_k: '150' },
  mode: 'fresh', quiet_seconds: '15',
});
out.slot = G.renderPopoverHtml({ agent_id: 'claude', percent: 50, used_tokens: 100, limit_tokens: 200 }, {})
  .includes('context-gauge-auto-compact-slot" hidden');

// Controller: flag off / failed read keep the slot hidden; unknown ids never fetch.
(async () => {
  const fetched = [];
  const mk = (body) => async (url) => { fetched.push(url); return { json: async () => body }; };
  const slot = () => ({ hidden: false, innerHTML: 'stale', querySelectorAll: () => [], querySelector: () => null });
  const s1 = slot();
  out.mountOff = await A.create({ fetch: mk({ success: true, available: false }) }).mount(s1, '12');
  out.slotOff = [s1.hidden, s1.innerHTML];
  const s2 = slot();
  out.mountErr = await A.create({ fetch: async () => { throw new Error('x'); } }).mount(s2, '12');
  out.slotErr = s2.hidden;
  const s3 = slot();
  out.mountOn = await A.create({ fetch: mk({ success: true, ...view }) }).mount(s3, 'db_session_12');
  out.slotOn = s3.hidden;
  out.mountBadId = await A.create({ fetch: mk({}) }).mount(slot(), 'local-draft');
  out.fetched = fetched;
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def result():
    env = dict(os.environ, CHAT_JS=str(JS / "chat/chat_auto_compact.js"),
               SETTINGS_JS=str(JS / "settings/settings_auto_compact.js"),
               GAUGE_JS=str(JS / "chat/chat_context_gauge.js"))
    proc = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_section_model(result):
    m = result["model"]
    assert m["visible"] and m["enabled"] == "inherit" and m["milestone"] == "inherit"
    assert m["percent"] == "55" and m["percentPlaceholder"] == "60"
    assert m["inheritEnabled"] == "Default (on)"
    assert m["rule"] == "Compacts between turns at 55% or 250k tokens; 40% at a Tasks milestone."
    assert m["scopeText"] == "Uses the supervised chat defaults."
    assert m["lastText"] == "Last auto-compact 82% → 14% (milestone)"
    assert result["hidden"] == {"visible": False}
    assert result["off"] == "Off for this supervised chat."
    assert result["failing"] is True


def test_section_markup(result):
    html = result["html"]
    assert 'data-field="enabled"' in html and 'data-field="milestone"' in html
    assert 'value="55" placeholder="60"' in html
    assert '<option value="inherit" selected>Default (on)</option>' in html
    assert result["offHtml"] == ""


def test_override_body(result):
    assert result["override"] == [
        {"enabled": None, "milestone": False, "percent": None},
        {"enabled": True, "milestone": None, "percent": 65},
    ]


def test_settings_patch(result):
    assert result["patch"] == {
        "chats": {"enabled": True, "percent": 70, "max_tokens": 300000},
        "managed": {"enabled": False, "percent": 55, "max_tokens": 0},
        "milestone": {"enabled": True, "percent": 40, "max_tokens": 150000},
        "mode": "fresh", "quiet_seconds": 15,
    }


def test_gauge_has_hidden_slot(result):
    assert result["slot"] is True


def test_controller_hides_until_available(result):
    assert result["mountOff"] is False and result["slotOff"] == [True, ""]
    assert result["mountErr"] is False and result["slotErr"] is True
    assert result["mountOn"] is True and result["slotOn"] is False
    assert result["mountBadId"] is False
    assert result["fetched"] == ["/api/auto-compact/chats/12", "/api/auto-compact/chats/db_session_12"]
