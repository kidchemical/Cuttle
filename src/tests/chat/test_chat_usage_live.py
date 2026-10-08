"""Harness /usage + /usage-live + /cost palette entries (CuttleUsageLive).

Behavioral characterization of harnessUsageCommands in
src/web/js/chat/chat_usage_live.js under node. chat_page.js keeps only
composition (sticky-chip state in, entries out); the /usage tables stay
in chat_slash.js (single owner).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_usage_live.js"
PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const U = require(process.env.MOD_JS);
const out = {};
const usageTable = {
  muse: { prefix: '/usage', label: 'Usage', hint: 'muse usage',
    category: 'muse-cmd', keywords: 'muse' },
  claude: { prefix: '/usage', label: 'Usage', hint: 'claude usage',
    category: 'claude-cmd', keywords: 'claude' },
};
const costCommand = (agent) => ({ prefix: '/cost', label: 'Cost',
  hint: agent + ' prices', category: agent + '-cmd', keywords: agent });
const deps = (active) => ({ isActive: (a) => a === active, usageTable, costCommand });
out.claude = U.harnessUsageCommands(deps('claude'));
out.priority = U.harnessUsageCommands(
  { isActive: (a) => a === 'muse' || a === 'claude', usageTable, costCommand });
out.none = U.harnessUsageCommands(deps('cursor'));
out.costOnly = U.harnessUsageCommands(deps('deepseek'));
const first = U.harnessUsageCommands(deps('muse'));
first[0].prefix = '/MUTATED';
out.copy = [usageTable.muse.prefix, first.length];
out.empty = U.harnessUsageCommands();
process.stdout.write(JSON.stringify(out));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_claude_usage_entries():
    res = _run()
    cmds = res["claude"]
    assert len(cmds) == 3
    assert cmds[0] == {"prefix": "/usage", "label": "Usage",
                       "hint": "claude usage", "category": "claude-cmd",
                       "keywords": "claude"}
    assert cmds[1]["prefix"] == "/usage-live"
    assert cmds[1]["label"] == "Live usage"
    assert "shared across panes" in cmds[1]["hint"]
    assert cmds[1]["category"] == "claude-cmd"  # live entry keeps the badge category
    assert cmds[2] == {"prefix": "/cost", "label": "Cost",
                       "hint": "claude prices", "category": "claude-cmd",
                       "keywords": "claude"}


@node_only
def test_first_active_agent_wins_and_cost_only_agents():
    res = _run()
    pri = res["priority"]
    assert pri[0]["category"] == "muse-cmd"  # muse precedes claude in check order
    assert res["none"] == []  # no harness badge → no entries
    cost = res["costOnly"]
    assert len(cost) == 1  # deepseek has no /usage entry, still gets /cost
    assert cost[0]["prefix"] == "/cost"
    assert cost[0]["category"] == "deepseek-cmd"


@node_only
def test_entries_are_copies_and_empty_is_safe():
    res = _run()
    assert res["copy"] == ["/usage", 3]  # mutating output never poisons the table
    assert res["empty"] == []


@node_only
def test_chat_page_delegates_usage_entries_to_usage_live_owner():
    src = PAGE_JS.read_text(encoding="utf-8")
    assert "CuttleUsageLive.harnessUsageCommands(" in src


@node_only
def test_chat_usage_live_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
