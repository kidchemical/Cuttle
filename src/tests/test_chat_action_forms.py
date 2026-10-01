"""Action-forms frontend domain: card interpretation and presentation decisions.

Behavioral characterization of src/web/js/chat_action_forms.js under node
(Phase 3 Slice 3). Covers cancel/side-effect detection, watch
interpretation, elapsed/bars formatting, restart progress decisions,
linkage detection, and invalid-metadata edges. DOM rendering, fetch
transport, timers, and message-history integration stay in chat_page.js
and are covered by the backend suites (test_action_forms.py,
test_action_form_process_restart.py, test_action_form_routes.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat_action_forms.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const A = require(process.env.MOD_JS);
const out = {};
// cancel / side-effect model
out.cancelExplicit = A.isExplicitActionFormCancelOption({ cancel: true });
out.cancelDismiss = A.isExplicitActionFormCancelOption({ id: 'x', action: '__dismiss__' });
out.cancelId = A.isExplicitActionFormCancelOption({ id: 'Cancel' });
out.cancelQa = A.isExplicitActionFormCancelOption({ id: 'a', label: 'A' });
out.cancelNull = A.isExplicitActionFormCancelOption(null);
out.sideQa = A.actionFormHasSideEffect({ mode: 'choice', options: [{ id: 'a' }] });
out.sideWatch = A.actionFormHasSideEffect({ watch: { url: '/output/x.json' } });
out.sideSubmit = A.actionFormHasSideEffect({ submit: { action: 'git.push' } });
out.sideAction = A.actionFormHasSideEffect({ options: [{ id: 'a', action: 'git.push' }] });
out.sideCancelOnly = A.actionFormHasSideEffect({ options: [{ id: 'cancel' }] });
out.sideNull = A.actionFormHasSideEffect(null);
out.isWatchResume = A.isWatchFormAction('__watch_resume__');
out.isWatchPark = A.isWatchFormAction('__watch_park__');
out.isWatchCancel = A.isWatchFormAction('__watch_cancel__');
out.isWatchNo = A.isWatchFormAction('flask.restart');
// restart recognition
out.restartByAction = A.specLooksLikeFlaskRestart(
  { options: [{ id: 'a', action: 'flask.restart' }] }, 'other-id');
out.restartByTitle = A.specLooksLikeFlaskRestart({ title: 'Restart Flask', options: [] }, 'x');
out.restartByIdAlone = A.specLooksLikeFlaskRestart({ title: 'Push', options: [] }, 'flask-restart-g3');
out.restartNo = A.specLooksLikeFlaskRestart({ title: 'Push', options: [] }, 'git-1');
out.epoch = A.flaskRestartFormEpoch('flask-restart-g12');
out.epochBad = A.flaskRestartFormEpoch('git-1');
out.linked = A.isFlaskRestartLinked({ options: [{ action: 'flask.restart' }] }, 'x');
out.linkedNo = A.isFlaskRestartLinked({ title: 'Push', options: [] }, 'git-1');
out.linkedNull = A.isFlaskRestartLinked(null, '');
out.groupExplicit = A.linkedRestartFormId({ formId: 'abc', group: 'flask-restart-g2' });
out.groupEpoch = A.linkedRestartFormId({ formId: 'flask-restart-g2', group: '' });
out.groupFallback = A.linkedRestartFormId({ formId: 'abc', group: '' });
// watch interpretation
out.inferExplicit = A.inferActionFormWatch({ watch: { url: '/output/j.json', interval_ms: 1000 } });
out.inferBtn = A.inferActionFormWatch(
  { options: [{ action: '__watch_resume__', params: { url: '/output/k.json' } }] });
out.inferNone = A.inferActionFormWatch({ options: [{ id: 'a' }] });
out.safeOk = [A.safeActionFormWatchUrl('/output/a.json'), A.safeActionFormWatchUrl('/api/x')];
out.safeBad = [A.safeActionFormWatchUrl('https://evil/x'), A.safeActionFormWatchUrl(''), A.safeActionFormWatchUrl(null)];
out.runKey = [A.watchRunKey({ run_id: 'r1' }), A.watchRunKey({ started_at: 's' }), A.watchRunKey(null), A.watchRunKey({})];
out.bind = [A.cardWatchBind({ run_id: 'r1' }), A.cardWatchBind(null)];
out.snap = A.watchSnapshotFromSpec({ snapshot: { state: 'running' } });
out.snapNone = A.watchSnapshotFromSpec({});
out.termWatch = A.watchIsTerminalState({ terminal: true }, { state: 'running' });
out.termDone = A.watchIsTerminalState({}, { state: 'done' });
out.termRunning = A.watchIsTerminalState({}, { state: 'running' });
out.termCustom = A.watchIsTerminalState({}, { state: 'ok' }, ['ok'], ['bad']);
out.key = A.actionFormWatchStorageKey({ formId: 'f1', watch: {}, sessionId: 'db_session_9' });
out.keyFallback = A.actionFormWatchStorageKey({ formId: '', watch: { id: 'job', run_id: 'r' }, sessionId: 's' });
// elapsed + bars
out.elapsedHms = A.formatWatchElapsedSeconds(3721);
out.elapsedMin = A.formatWatchElapsedSeconds(125);
out.elapsedSec = A.formatWatchElapsedSeconds(7);
out.elapsedNeg = A.formatWatchElapsedSeconds(-5);
out.elapsedDone = A.watchElapsedText({ state: 'done', elapsed: '3m 12s' });
out.elapsedDoneSec = A.watchElapsedText({ state: 'failed', elapsed_sec: 90 });
out.elapsedEmpty = A.watchElapsedText(null);
out.barsEmpty = A.normalizeWatchBars({});
out.barsSingle = A.normalizeWatchBars({ percent: 150, label: 'L' });
out.barsMulti = A.normalizeWatchBars({ percent: 10, label: 'L', bars: [
  { id: 'a', percent: 50 }, { label: 'B', percent: 200, kind: 'weird' },
  { detail: 'x'.repeat(200) }] });
// restart progress presentation
out.labelWaiting = A.restartProgressLabel({ state: 'waiting_for_idle' }, {});
out.labelWaitingN = A.restartProgressLabel({ state: 'waiting_for_idle' }, { active_count: 2 });
out.labelWaiting1 = A.restartProgressLabel({ state: 'waiting_for_idle' }, { active_count: 1 });
out.labelHandoff = A.restartProgressLabel({ state: 'preparing' }, null);
out.labelStop = A.restartProgressLabel({ state: 'stopping_old_flask' }, null);
out.labelHealthy = A.restartProgressLabel({ state: 'healthy', health_ms: 6700, new_flask_pid: 123 }, null);
out.labelHealthyBare = A.restartProgressLabel({ state: 'healthy' }, null);
out.labelFailed = A.restartProgressLabel({ state: 'failed', error: 'boom' }, null);
out.labelTimedOut = A.restartProgressLabel({ state: 'timed_out' }, null);
out.labelRejected = A.restartProgressLabel({ state: 'rejected' }, null);
out.labelCancelled = A.restartProgressLabel({ state: 'cancelled' }, null);
out.labelUnknown = A.restartProgressLabel({ state: 'bogus' }, null);
out.labelNull = A.restartProgressLabel(null, null);
out.pct = [A.restartProgressPercent('acknowledged'), A.restartProgressPercent('healthy'),
  A.restartProgressPercent('bogus')];
out.termStates = [A.isRestartTerminalState('healthy'), A.isRestartTerminalState('failed'),
  A.isRestartTerminalState('timed_out'), A.isRestartTerminalState('rejected'),
  A.isRestartTerminalState('cancelled'), A.isRestartTerminalState('running'),
  A.isRestartTerminalState(''), A.isRestartTerminalState(null)];
process.stdout.write(JSON.stringify(out));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_cancel_and_side_effect_model():
    res = _run()
    assert res["cancelExplicit"] is True
    assert res["cancelDismiss"] is True
    assert res["cancelId"] is True
    assert res["cancelQa"] is False  # Q&A picks must not collapse to Cancelled
    assert res["cancelNull"] is False
    assert res["sideQa"] is False
    assert res["sideWatch"] is True
    assert res["sideSubmit"] is True
    assert res["sideAction"] is True
    assert res["sideCancelOnly"] is False
    assert res["sideNull"] is False
    assert res["isWatchResume"] is True
    assert res["isWatchPark"] is True
    assert res["isWatchCancel"] is True
    assert res["isWatchNo"] is False


@node_only
def test_restart_recognition_and_linkage():
    res = _run()
    assert res["restartByAction"] is True
    assert res["restartByTitle"] is True
    assert res["restartByIdAlone"] is False  # id alone never suffices
    assert res["restartNo"] is False
    assert res["epoch"] == 12
    assert res["epochBad"] is None
    assert res["linked"] is True
    assert res["linkedNo"] is False
    assert res["linkedNull"] is False
    assert res["groupExplicit"] == "flask-restart-g2"
    assert res["groupEpoch"] == "flask-restart-g2"
    assert res["groupFallback"] == "abc"


@node_only
def test_watch_interpretation():
    res = _run()
    assert res["inferExplicit"] == {"url": "/output/j.json", "interval_ms": 1000}
    assert res["inferBtn"]["url"] == "/output/k.json"
    assert res["inferBtn"]["done_states"] == ["done", "trellis_ok"]
    assert res["inferNone"] is None
    assert res["safeOk"] == ["/output/a.json", "/api/x"]
    assert res["safeBad"] == ["", "", ""]
    assert res["runKey"] == ["r1", "s", "", ""]
    assert res["bind"] == ["r1", ""]
    assert res["snap"] == {"state": "running"}
    assert res["snapNone"] is None
    assert res["termWatch"] is True
    assert res["termDone"] is True
    assert res["termRunning"] is False
    assert res["termCustom"] is True
    assert res["key"] == "cuttle.formWatch.db_session_9.f1"
    assert res["keyFallback"] == "cuttle.formWatch.s.job.r"


@node_only
def test_elapsed_and_bars():
    res = _run()
    assert res["elapsedHms"] == "1h 02m 01s"
    assert res["elapsedMin"] == "2m 05s"
    assert res["elapsedSec"] == "7s"
    assert res["elapsedNeg"] == "0s"
    assert res["elapsedDone"] == "3m 12s"
    assert res["elapsedDoneSec"] == "1m 30s"
    assert res["elapsedEmpty"] == ""
    assert res["barsEmpty"] == [{"id": "overall", "label": "Overall",
                                 "percent": 0, "kind": "primary"}]
    assert res["barsSingle"] == [{"id": "overall", "label": "L",
                                  "percent": 100, "kind": "primary"}]
    multi = res["barsMulti"]
    assert [b["kind"] for b in multi] == ["primary", "worker", "worker"]
    assert multi[0]["percent"] == 50 and multi[1]["percent"] == 100
    assert multi[0]["id"] == "a" and multi[1]["label"] == "B"
    assert len(multi[2]["detail"]) == 160


@node_only
def test_restart_progress_presentation():
    res = _run()
    assert res["labelWaiting"] == "Waiting for active work to finish…"
    assert res["labelWaitingN"] == "Waiting for 2 active tasks to finish…"
    assert res["labelWaiting1"] == "Waiting for 1 active task to finish…"
    assert res["labelHandoff"] == "Handing off to the daemon…"
    assert res["labelStop"] == "Stopping Flask…"
    assert res["labelHealthy"] == "Flask restarted — PID 123, 6.7s"
    assert res["labelHealthyBare"] == "Flask restarted"
    assert res["labelFailed"] == "Restart failed — boom"
    assert res["labelTimedOut"] == "Restart timed out — see daemon logs"
    assert res["labelRejected"] == "Restart postponed — other work is still running"
    assert res["labelCancelled"] == "Restart cancelled"
    assert res["labelUnknown"] == "Restarting Flask…"
    assert res["labelNull"] == "Restarting Flask…"
    assert res["pct"] == [22, 100, None]
    assert res["termStates"] == [True, True, True, True, True, False, False, False]


@node_only
def test_chat_action_forms_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
