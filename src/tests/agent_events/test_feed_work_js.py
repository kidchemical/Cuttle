"""Pure JS models for background work: Agent Feed "Running now" + chat bubble."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "js"
node_only = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

HARNESS = r"""
const W = require(process.env.WEB + '/agent_events/feed_work_model.js') && globalThis.CuttleAgentWork;
require(process.env.WEB + '/chat/chat_background_work.js');
const B = globalThis.CuttleChatBackgroundWork;
const owner = {
  chat_session_id: '1821', handle: 'CH-001821', state: 'waiting', label: 'Waiting on: fg12',
  run: null, process_count: 3, detached_count: 1,
  waits: [{ job_id: 'claude-bg-1821-x', label: 'Waiting on: fg12', started_at: '2026-11-01T01:53:20' }],
  watches: [{ job_id: 'build', label: 'Build', percent: 140 }],
  schedules: [{ id: 'op1', label: 'Supervise CH-001821', detail: 'every 15m', status: 'running', cancellable: true }],
  processes: [{ pid: 10, name: 'claude', children: [{ pid: 11, name: 'bash', children: [] }] },
              { pid: 20, name: 'run-gate.sh', detached: true, children: [] }],
  children: [{ chat_session_id: '1862', state: 'running', process_count: 1, children: [] }],
};
const tree = { owners: [owner], mesh: [], totals: { chats: 2, processes: 4, detached: 1 } };
const out = {
  elapsed: [W.elapsed(5), W.elapsed(125), W.elapsed(3 * 3600 + 240), W.elapsed(-1)],
  handle: W.handle('7'), bytes: [W.bytes(0), W.bytes(5 * 1024 * 1024), W.bytes(3 * 1024 ** 3)],
  cmd: W.shortCommand('/home/u/.local/bin/claude -p --model x', 20),
  state: [W.stateLabel(owner), W.stateLabel({ state: 'processes', detached_count: 2 })],
  badges: W.ownerBadges(owner).map((b) => b.text),
  lines: W.ownerLines(owner).map((l) => [l.kind, l.text, l.percent == null ? null : l.percent]),
  stop: [W.canStop(owner), W.canStop({ process_count: 2 }), W.canEndAll({ process_count: 2 }), W.canEndAll({ schedules: [1] })],
  prompt: W.endAllPrompt(owner), procPrompt: W.endProcessPrompt(owner.processes[0]),
  totals: W.totalsText(tree), empty: [W.isEmpty(tree), W.isEmpty({ owners: [], mesh: [] })],
  sigStable: W.signature(tree) === W.signature(JSON.parse(JSON.stringify(tree))),
  sigChanges: W.signature(tree) !== W.signature({ ...tree, owners: [{ ...owner, detached_count: 2 }] }),
  plan: B.plan({ label: 'Waiting on: fg12', process_count: 3, detached_count: 1, feed: true, started_at: 100,
    waits: [{ job_id: 'j', started_at: 50 }], watches: [], schedules: [{ label: 'Supervise CH-1', detail: 'every 15m' }] }),
  planProcs: B.plan({ process_count: 2, detached_count: 2, waits: [], watches: [], schedules: [] }),
  planSched: B.plan({ label: 'x', process_count: 0, waits: [], watches: [], schedules: [{ label: 'Supervise' }] }),
  planNone: [B.plan(null), B.plan({ process_count: 0, waits: [], watches: [], schedules: [] })],
};
process.stdout.write(JSON.stringify(out));
"""


@node_only
def test_models():
    import os

    proc = subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, timeout=30,
                          env={"PATH": os.environ["PATH"], "WEB": str(WEB)})
    assert proc.returncode == 0, proc.stderr
    r = json.loads(proc.stdout)
    assert r["elapsed"] == ["5s", "2m", "3h 04m", ""]
    assert r["handle"] == "CH-000007"
    assert r["bytes"] == ["", "5 MB", "3.0 GB"]
    assert r["cmd"] == "claude -p --model x"
    assert r["state"] == ["Waiting in background", "Detached processes running"]
    assert r["badges"] == ["3 processes", "1 detached", "1 sub-agent"]
    assert r["lines"] == [["wait", "Waiting on: fg12", None], ["watch", "Build", 100],
                          ["schedule", "Supervise CH-001821 · every 15m", None]]
    assert r["stop"] == [True, False, True, False]
    assert r["prompt"] == "End all work in CH-001821: stop its agent and end 3 processes?"
    assert r["procPrompt"] == "End claude (pid 10) and its 1 child process?"
    assert r["totals"] == "2 chats · 4 processes · 1 detached"
    assert r["empty"] == [False, True]
    assert r["sigStable"] and r["sigChanges"]

    p = r["plan"]
    assert p["title"] == "Waiting in background" and p["label"] == "Waiting on: fg12"
    assert p["meta"] == ["3 processes (1 detached)", "Supervise CH-1 · every 15m"]
    assert p["stop"] == "chat" and p["started"] == 50 and p["feed"] is True and p["working"] is True
    assert r["planProcs"]["title"] == "Detached processes running" and r["planProcs"]["stop"] == "processes"
    assert r["planSched"]["working"] is False and r["planSched"]["stop"] is None
    assert r["planNone"] == [None, None]
