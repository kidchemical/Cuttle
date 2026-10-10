"""Frontend context gauge domain (chat_context_gauge.js).

Behavioral characterization of the composer agent-context radial moved out of
``chat_page.js`` (Phase 2 slice 2E). Covers the pure decisions — token-count
formatting, agent-hint normalization, sticky-agent detection from slash chips,
the ring/aria/tooltip DOM plan, the usage->status seed math (including the
Cursor aggregated-usage guard) and popover markup — plus the controller
factory contract, all under node (skipped when node is absent).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_context_gauge.js"
USAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_usage.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = r"""
const G = require(process.env.MOD_JS);
const usage = require(process.env.USAGE_JS);
const normalize = usage.normalize;
const out = {};

out.fmt = [0, 999, 1000, 1500, 99999, 100000, 150000, 1000000, 1500000, 12000000]
  .map(G.formatContextTokenCount);
out.norm = ["", "cursor", "/cursor", "Cursor ", "opencode_x", "muse code",
  "gpt-4o", "codex"].map(G.normalizeAgentHint);

out.sticky = {
  prefix: G.stickyAgentIdFromSlashCtx({ chat: { chips: [{ prefix: '/cursor ' }] }, welcome: { chips: [] } }),
  label: G.stickyAgentIdFromSlashCtx({ chat: { chips: [{ label: 'Muse Code', category: 'muse' }] }, welcome: { chips: [] } }),
  opencode: G.stickyAgentIdFromSlashCtx({ chat: { chips: [{ label: 'OpenCode Zen' }] }, welcome: { chips: [] } }),
  codex: G.stickyAgentIdFromSlashCtx({ chat: { chips: [{ prefix: '/codex ' }] }, welcome: { chips: [] } }),
  model: G.stickyAgentIdFromSlashCtx({ chat: { chips: [{ prefix: '/model auto' }] }, welcome: { chips: [] } }),
  none: G.stickyAgentIdFromSlashCtx({}),
};

function plan(percent, used, limit, token_source, hint) {
  const p = G.gaugeDomPlan(
    { percent, used_tokens: used, limit_tokens: limit, token_source, hint, agent_id: 'cursor' },
    'cursor');
  return { dash: p.dashArray, warn: p.warn, hot: p.hot, title: p.title, aria: p.ariaLabel };
}
out.dom = {
  zero: plan(0, 0, 1000000),
  p50: plan(50, 500000, 1000000),
  p60: plan(60, 600000, 1000000),
  p85: plan(85, 850000, 1000000),
  p100: plan(100, 1000000, 1000000),
  agg: plan(0, 0, 1000000, 'aggregated'),
  hint: plan(30, 300000, 1000000, null, 'Health ok'),
  fallback: (function () { const p = G.gaugeDomPlan({ percent: 0 }, null); return { aria: p.ariaLabel, agent: p.agent }; })(),
};

function st(u, agent, prev) {
  const s = G.computeUsageStatus(u, agent, prev || null, normalize);
  if (!s) return null;
  return { agent: s.agent_id, used: s.used_tokens, limit: s.limit_tokens,
    pct: s.percent, compact: s.compact_available, hint: s.hint, model: s.model };
}
out.status = {
  cursorPeak: st({ context_tokens: 18000, prompt_tokens: 12000, cacheReadTokens: 0 }, 'cursor'),
  cursorAgg: st({ inputTokens: 109965, cacheReadTokens: 921344, context_tokens: 1031309, prompt_tokens: 109965 }, 'cursor'),
  cursorPeakWithCache: st({ context_tokens: 400000, prompt_tokens: 300000, cacheReadTokens: 90000 }, 'cursor'),
  opencode: st({ context_tokens: 55000, prompt_tokens: 40000 }, 'opencode'),
  codex: st({ context_tokens: 24763 }, 'codex'),
  museFilling: st({ context_tokens: 600000 }, 'muse'),
  museNearlyFull: st({ context_tokens: 900000 }, 'muse'),
  prevLimit: st({ context_tokens: 300000 }, 'cursor', { limit_tokens: 200000 }),
  noStamp: st({ prompt_tokens: 40000 }, 'opencode'),
  invalid: st({ context_tokens: 1000 }, 'nope'),
};

out.popover = {
  meter: G.renderPopoverHtml({ agent_id: 'cursor', percent: 42, used_tokens: 420000, limit_tokens: 1000000, model: 'gpt', hint: 'ok' }, { escapeHtml: (s) => String(s) }),
  busy: G.renderPopoverHtml({ agent_id: 'cursor', percent: 42 }, { escapeHtml: (s) => String(s), busy: true }),
  noCompact: G.renderPopoverHtml({ agent_id: 'cursor', percent: 42, compact_available: false }, { escapeHtml: (s) => String(s) }),
  aggregated: G.renderPopoverHtml({ agent_id: 'cursor', percent: 0, used_tokens: 0, token_source: 'aggregated' }, { escapeHtml: (s) => String(s) }),
};
process.stdout.write(JSON.stringify(out));
"""


def _run(script=None):
    import os
    proc = subprocess.run(
        ["node", "-e", script or HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS),
             "USAGE_JS": str(USAGE_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_token_count_formatting():
    res = _run()
    assert res["fmt"] == ["0", "999", "1k", "1.5k", "100k", "100k",
                          "150k", "1M", "1.5M", "12M"]


@node_only
def test_normalize_agent_hint():
    res = _run()
    assert res["norm"] == [None, "cursor", "cursor", "cursor", "opencode",
                           "muse", None, "codex"]


@node_only
def test_sticky_agent_from_slash_chips():
    res = _run()
    assert res["sticky"] == {
        "prefix": "cursor",
        "label": "muse",
        "opencode": "opencode",
        "codex": "codex",
        "model": None,
        "none": None,
    }


@node_only
def test_gauge_dom_plan():
    res = _run()
    dom = res["dom"]
    assert dom["zero"] == {
        "dash": "0 100", "warn": False, "hot": False,
        "title": "Cursor context · 0% · 0 / 1M · Click for details / Compact",
        "aria": "cursor context 0 percent",
    }
    assert dom["p50"]["dash"] == "50 50" and dom["p50"]["warn"] is False
    assert dom["p60"]["warn"] is True and dom["p60"]["hot"] is False
    assert dom["p85"]["hot"] is True and dom["p85"]["warn"] is False
    assert dom["p100"] == {
        "dash": "100 0", "warn": False, "hot": True,
        "title": "Cursor context · 100% · 1M / 1M · Click for details / Compact",
        "aria": "cursor context 100 percent",
    }
    assert dom["agg"] == {
        "dash": "0 100", "warn": False, "hot": False,
        "title": "Cursor context · awaiting next reply · Click for details / Compact",
        "aria": "cursor context awaiting next reply",
    }
    assert dom["hint"]["title"] == (
        "Cursor context · 30% · 300k / 1M · Health ok · Click for details / Compact"
    )
    assert dom["fallback"] == {"aria": "agent context 0 percent", "agent": "agent"}


@node_only
def test_compute_usage_status_math():
    res = _run()
    s = res["status"]
    assert s["cursorPeak"] == {
        "agent": "cursor", "used": 18000, "limit": 1000000, "pct": 1.8,
        "compact": True, "hint": "Context looks healthy.", "model": "",
    }
    # Cursor billing total stamped as a peak must not seed the ring.
    assert s["cursorAgg"] is None
    assert s["cursorPeakWithCache"]["used"] == 400000
    assert s["cursorPeakWithCache"]["pct"] == 40
    assert s["opencode"] == {
        "agent": "opencode", "used": 55000, "limit": 200000, "pct": 27.5,
        "compact": True, "hint": "Context looks healthy.", "model": "",
    }
    assert s["codex"]["limit"] == 200000 and s["codex"]["pct"] == 12.4
    assert s["museFilling"]["pct"] == 60
    assert "filling up" in s["museFilling"]["hint"]
    assert s["museNearlyFull"]["pct"] == 90
    assert "nearly full" in s["museNearlyFull"]["hint"]
    # A prior status's limit wins over the per-agent default.
    assert s["prevLimit"]["limit"] == 200000 and s["prevLimit"]["pct"] == 100
    assert s["noStamp"] is None
    assert s["invalid"] is None


@node_only
def test_render_popover_html():
    res = _run()
    p = res["popover"]
    assert "42%" in p["meter"]
    assert "420k / 1M" in p["meter"]
    assert "gpt" in p["meter"]
    assert "ok" in p["meter"]
    assert "disabled" in p["busy"] and "Compacting…" in p["busy"]
    assert "context-gauge-compact-btn" not in p["noCompact"]
    assert "waiting for next reply" in p["aggregated"]


@node_only
def test_module_parses():
    proc = subprocess.run(["node", "--check", str(MOD_JS)],
                          capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr


@node_only
def test_controller_factory_contract():
    probe = (
        "const G = require(process.env.MOD_JS);\n"
        "const assert = require('assert');\n"
        "const arc = { setAttribute() {} };\n"
        "const el = { hidden: true, innerHTML: '', isConnected: true,\n"
        "  dataset: {}, addEventListener() {}, getAttribute() { return null; },\n"
        "  setAttribute() {}, querySelector: (s) => s === '.context-gauge-arc' ? arc : null,\n"
        "  classList: { add() {}, remove() {}, toggle() {} }, appendChild() {} };\n"
        "const doc = { getElementById: () => el, createElement: () => el,\n"
        "  body: { appendChild() {} }, addEventListener() {} };\n"
        "let timers = 0;\n"
        "const win = { document: doc, innerWidth: 1200,\n"
        "  setTimeout: () => { timers += 1; return timers; }, clearTimeout() {} };\n"
        "const slashCtx = { chat: { chips: [{ prefix: '/cursor ' }] }, welcome: { chips: [] } };\n"
        "const ctl = G.create({ win, fetch: () => Promise.reject(new Error('x')),\n"
        "  escapeHtml: (s) => String(s), normalizeUsage: (u) => u,\n"
        "  getSlashCtx: () => slashCtx, getSessionId: () => null,\n"
        "  getProjectPath: () => '', toast() {} });\n"
        "for (const m of ['init', 'noteUsage', 'scheduleRefresh', 'stickyAgentId']) {\n"
        "  assert.strictEqual(typeof ctl[m], 'function', m);\n"
        "}\n"
        "assert.strictEqual(ctl.stickyAgentId(), 'cursor');\n"
        "ctl.noteUsage({ context_tokens: 1000 }, 'cursor');\n"
        "ctl.scheduleRefresh(60);\n"
        "ctl.init();\n"
        "assert.ok(timers >= 2, 'scheduleRefresh must arm a timer');\n"
        "console.log('controller contract ok');\n"
    )
    proc = subprocess.run(["node", "-e", probe], capture_output=True, text=True,
                          encoding="utf-8",
                          env={"PATH": __import__("os").environ["PATH"],
                               "MOD_JS": str(MOD_JS)})
    assert proc.returncode == 0, proc.stderr
    assert "controller contract ok" in proc.stdout
