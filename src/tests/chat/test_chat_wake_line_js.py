"""The chat's wake line: what starts an idle chat's next turn (pure JS model)."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

HARNESS = r"""
require(process.env.WEB + '/chat/chat_background_work.js');
const B = globalThis.CuttleChatBackgroundWork;
const sup = (status, next_at) => ({ id: 's', supervisor: 'CH-002404', status, every: 1800, next_at });
const out = {
  plain: B.plan(null, [], ''),
  processesOnly: B.wake({ process_count: 2 }, ''),
  orchestratingAlone: B.plan(null, [], 'active'),
  supervised: B.plan({ watchers: [sup('running', 9000), sup('running', 5000)] }, [], 'active'),
  paused: B.wake({ watchers: [sup('paused', null)] }, 'active'),
  job: B.wake({ waits: [{ label: 'Build 46 on the laptop' }], watchers: [sup('running', 5000)] }, 'active'),
  unit: B.wake({ watches: [{ kind: 'unit', label: 'Bake' }, { kind: 'unit', label: 'Bench' }] }, ''),
  boundWatch: B.wake({ watches: [{ job_id: 'x', label: 'Progress only' }] }, ''),
  subagents: B.wake({ waits: [{ label: 'python -m api.subagents spawn --wait' }] }, 'active'),
  held: B.wake({ waits: [{ label: 'Build' }] }, 'paused'),
  withJobs: B.plan({ waits: [{ job_id: 'j', label: 'Build' }], process_count: 1 }, [], ''),
};
console.log(JSON.stringify(out));
"""


def test_wake_line_says_what_starts_the_next_turn():
    out = json.loads(subprocess.run(["node", "-e", HARNESS], capture_output=True, text=True, check=True,
                                    env={**os.environ, "WEB": str(WEB)}).stdout)
    # An ordinary idle chat shows no card and no wake line.
    assert out["plain"] is None and out["processesOnly"] is None and out["boundWatch"] is None
    # An orchestrating chat nobody watches says so instead of just looking idle.
    alone = out["orchestratingAlone"]
    assert alone["state"] == "idle" and alone["title"] == "Idle"
    assert alone["wake"]["kind"] == "none" and "Nothing is set to wake this chat" in alone["wake"]["text"]
    # Supervised and idle: the earliest check, with its supervisor and countdown facts.
    sup = out["supervised"]
    assert sup["title"] == "Supervised" and sup["working"] is False
    assert sup["wake"] == {"kind": "supervisor", "tone": "ok", "supervisor": "CH-002404", "at": 5000,
                           "every": 1800, "text": "Idle until its supervisor checks on it"}
    assert out["paused"]["kind"] == "none" and out["paused"]["tone"] == "warn"
    assert "Supervision is paused" in out["paused"]["text"]
    # Its own work wins over the supervisor: that is what resumes it first.
    assert out["job"] == {"kind": "self", "tone": "ok", "text": "Resumes by itself when “Build 46 on the laptop” ends"}
    assert out["unit"]["text"] == "Resumes by itself when its background work ends"
    assert out["subagents"]["text"] == "Resumes by itself when its sub-agents finish"
    # A held orchestration holds its wake-ups too.
    assert out["held"]["kind"] == "held" and "Orchestration paused" in out["held"]["text"]
    assert out["withJobs"]["title"] == "Waiting in background" and out["withJobs"]["wake"]["kind"] == "self"
