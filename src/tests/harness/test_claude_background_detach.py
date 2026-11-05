"""Claude Code background waits detach from the turn (``api.agent_harness.background_wait``).

A root ``result`` with background tasks pending ends the turn with its reply
plus a watch card; the CLI keeps running, later answers land as new bubbles,
a message sent meanwhile steers into the live run, and Stop still kills it.
Runs against a fake ``claude`` script (no real CLI, no network).
"""

import asyncio
import concurrent.futures
import os
import stat
import sys
import textwrap
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.agent_harness import background_wait as bw  # noqa: E402
from api.agent_harness import steer  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="fake CLI script uses a shebang")


@pytest.fixture(autouse=True)
def _clean_registries():
    from api import chat_live_status as live

    def _clear():
        with steer._lock:
            steer._active.clear()
        with bw._lock:
            bw._active.clear()
        with live._LOCK:
            live._STORE.clear()

    _clear()
    yield
    _clear()


def _live(chat_id):
    from api.chat_live_status import get_live_status

    return get_live_status(str(chat_id))


# Speaks just enough claude 2.1 stream-json: a first result while one
# background task runs, then (per MODE) the task's follow-up turn, an answer
# to a steer read from stdin, or nothing until EOF.
_FAKE_CLAUDE = r'''
import json, os, sys, threading, time, queue
mode = os.environ.get("FAKE_CLAUDE_MODE", "finishes")
q = queue.Queue()
def rd():
    for line in sys.stdin:
        q.put(json.loads(line))
    q.put(None)
threading.Thread(target=rd, daemon=True).start()
def out(msg):
    msg.setdefault("session_id", "cs-detach")
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()
def result(text):
    out({"type": "result", "subtype": "success", "result": text, "total_cost_usd": 0.01,
         "usage": {"input_tokens": 10, "output_tokens": 2}})
def bg(tasks):
    out({"type": "system", "subtype": "background_tasks_changed", "tasks": tasks})
q.get()
out({"type": "system", "subtype": "init", "model": "fake"})
if mode == "real_task":
    # Like Claude Code's run_in_background Bash: own session, stdout on the task file.
    import subprocess
    path = os.environ["FAKE_TASK_OUT"]
    fh = open(path, "w")
    subprocess.Popen([sys.executable, "-c", "import os, time; print('rendered 13 frames', flush=True); "
                      "time.sleep(float(os.environ['FAKE_TASK_SEC'])); print('blend saved', flush=True)"],
                     stdout=fh, stderr=fh, stdin=subprocess.DEVNULL, start_new_session=True)
    out({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
         "content": "Command running in background with ID: b7. Output is being written to: %s. "
                    "You will be notified when it completes." % path}]}})
    bg([{"task_id": "b7", "task_type": "local_bash", "description": "blender job"}])
    result("started the blender job")
    time.sleep(30)  # ignores EOF while its task runs
    sys.exit(0)
bg([{"task_id": "b1", "task_type": "local_bash", "description": "watch CI"}])
result("started the watcher")
if mode == "finishes":
    time.sleep(0.8)
    bg([])
    result("CI is green")
elif mode == "steer":
    m = q.get(timeout=15)
    if m is not None:
        out({"type": "user", "isReplay": True, "parent_tool_use_id": None, "message": m["message"]})
        out({"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m2"}}})
        time.sleep(1.0)  # working on the answer: the chat shows the typing bubble
        result("saw: %s" % m["message"]["content"])
        time.sleep(0.3)
        bg([])
        result("CI is green")
elif mode == "ignores_eof":
    time.sleep(30)
    sys.exit(0)
# Like the real CLI: exit on stdin EOF.
while q.get(timeout=30) is not None:
    pass
'''


def _fake_bin(tmp_path, body):
    path = tmp_path / "claude"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _until(pred, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return bool(pred())


def _start(tmp_path, monkeypatch, mode, chat_id, *, background=True):
    import scripts.utilities.claude_cli_tool as mod

    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    monkeypatch.setattr(mod, "claude_executable", lambda: _fake_bin(tmp_path, _FAKE_CLAUDE))
    monkeypatch.setattr(
        "scripts.utilities.claude_cli_session_store.save_claude_resume_id", lambda *a, **k: None
    )
    wait = bw.BackgroundWait(str(chat_id), "claude") if background else None
    started = time.monotonic()
    res = asyncio.run(
        mod.ClaudeCliTool().execute_prompt(
            "go", cwd=str(tmp_path), chat_session_id=str(chat_id), timeout=120,
            steerable=True, background=wait,
        )
    )
    return res, wait, time.monotonic() - started


def _gone(pid):
    """Process exited (``/proc`` probe; signalling a dead pid trips the test kill guard)."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] in ("Z", "X")
    except OSError:
        return True


def _status(wait):
    from api.job_watch import read_status

    return read_status(wait.job_id) or {}


class _Saver:
    def __init__(self):
        self.bodies = []

    def __call__(self, res):
        self.bodies.append(dict(res))

    def texts(self):
        return [b.get("response") for b in self.bodies]


@posix_only
def test_turn_ends_at_first_wait_with_watch_card_and_follow_up_is_a_new_bubble(tmp_path, monkeypatch):
    res, wait, took = _start(tmp_path, monkeypatch, "finishes", 6101)
    # The turn returns before the background task's follow-up turn.
    assert res["success"] is True
    assert res["background_job"] == wait.job_id
    assert res["output"].startswith("started the watcher")
    assert "CI is green" not in res["output"]
    assert "<cuttle_action_form>" in res["output"]
    assert f'"/output/{wait.job_id}-status.json"' in res["output"]
    assert "__watch_cancel__" in res["output"] and "__watch_resume__" not in res["output"]
    assert res["claude_session_id"] == "cs-detach"
    status = _status(wait)
    assert status["state"] == "running"
    assert status["label"] == "Waiting on: watch CI"
    assert status["started_at"] and status["pid"] > 0
    # The run stays steerable while it waits (token still registered).
    assert steer.active_agent(6101) == "claude"
    assert bw.detached_wait(6101, "claude") is wait

    saver = _Saver()
    assert bw.bind_saver(wait.job_id, saver, {"agent_id": "claude", "query_id": "q1", "response": "x",
                                               "background_job": wait.job_id, "usage": {"cost": 1}})
    assert _until(lambda: saver.texts() == ["CI is green"])
    body = saver.bodies[0]
    assert body["type"] == "claude_command"
    assert body["agent_id"] == "claude" and body["query_id"] == "q1"
    assert "background_job" not in body
    assert body["usage"]["prompt_tokens"] == 10
    assert _until(lambda: _status(wait).get("state") == "done")
    assert _status(wait)["percent"] == 100
    assert _until(lambda: steer.active_agent(6101) is None)
    assert bw.detached_wait(6101) is None


@posix_only
def test_saver_binds_follow_ups_and_persists_each_as_its_own_row(tmp_path, monkeypatch):
    """The saver that stored the first reply stores the follow-up as a second row."""
    from api.chat_turn_persist import make_assistant_saver

    rows = []

    class _DB:
        def add_message(self, sid, role, content, metadata=None):
            rows.append((role, content, metadata))
            return len(rows)

    res, wait, _ = _start(tmp_path, monkeypatch, "finishes", 6102)
    saver = make_assistant_saver(
        chat_session_id=6102, db=_DB(), request_data={}, resolve_project=lambda _d: str(tmp_path),
        assistant_meta_fn=lambda r: {"type": r.get("type")}, project_merge_fn=lambda m, *a: m,
        schedule_autoname=lambda *_a: None, project_root=tmp_path,
    )
    saver({"success": True, "response": res["output"], "type": "claude_command",
           "agent_id": "claude", "background_job": res["background_job"]})
    assert _until(lambda: len(rows) == 2)
    assert rows[0][1].startswith("started the watcher")
    assert rows[1] == ("assistant", "CI is green", {"type": "claude_command"})


@posix_only
def test_message_sent_while_waiting_steers_into_the_live_run(tmp_path, monkeypatch):
    from api import chat_delivery
    from api.chat_coordinator import AgentSelection, AgentTurnIO, PreparedAgentTurn, submit_agent_turn

    res, wait, _ = _start(tmp_path, monkeypatch, "steer", 6103)
    saver = _Saver()
    bw.bind_saver(wait.job_id, saver, {"agent_id": "claude"})
    ran, steered_rows = [], []
    io = AgentTurnIO(
        run_harness=lambda *a, **k: ran.append(a) or {"success": True, "response": "second run"},
        run_router=lambda **k: None, persist_user=lambda: ran.append("persist"),
        make_saver=lambda: None, should_save=lambda b: False, notify_mobile=None,
        format_shortcut=lambda *a: {}, persist_steered_user=steered_rows.append,
    )
    out = submit_agent_turn(
        PreparedAgentTurn(message="/claude and PINEAPPLE", session_id=6103),
        io=io, delivery=chat_delivery, claim=True,
        selection=AgentSelection(kind="harness", agent_id="claude", prompt="and PINEAPPLE"),
    )
    assert out.body["steered"] is True and out.body["no_reply"] is True
    assert ran == [], "no second claude run may start while the detached run is live"
    assert steered_rows == [{"steered": True, "steered_agent": "claude"}]
    assert chat_delivery.is_busy(6103) is False
    # While the run answers, every device sees a typing bubble: live status is
    # active and flagged as a follow-up to the reply already in the transcript.
    live = _live(6103)
    assert live["active"] is True and live["followup"] is True
    assert live["status"] == "Claude Code is answering your message…"
    assert wait.phase == "working"
    assert _until(lambda: saver.texts() == ["saw: and PINEAPPLE", "CI is green"])
    assert _until(lambda: _status(wait).get("state") == "done")
    # Both answers landed as bubbles; nothing is generating any more.
    assert _live(6103)["active"] is False
    assert not _live(6103).get("followup")
    assert wait.phase == "waiting"


def test_detached_run_phases_drive_live_status_and_the_card():
    """Waiting → card label only; working → live status (typing bubble); follow-up clears it."""
    from api.chat_live_status import set_live_status

    wait = bw.BackgroundWait("6109", "claude")
    wait.start(["render frames"], pid=None)
    sink = wait.status_sink()
    assert wait.phase == "waiting"
    assert _status(wait)["label"] == "Waiting on: render frames"
    # Heartbeats and tool lines while only the background task runs stay off the chat.
    sink.put(("status", "Claude Code working… 30s (waiting on background task)"))
    assert _live(6109)["active"] is False
    # A finished task (or a steer) puts the run to work: the line shows, flagged follow-up.
    wait.working("Background task finished — Claude Code is following up…")
    live = _live(6109)
    assert live["active"] is True and live["followup"] is True
    assert live["status"] == "Background task finished — Claude Code is following up…"
    sink.put(("status", "tool 1: Bash blender --background"))
    assert _live(6109)["status"] == "tool 1: Bash blender --background"
    # Idempotent while working; an explicit event replaces the line.
    wait.working("ignored")
    assert _live(6109)["status"] == "tool 1: Bash blender --background"
    wait.working("Claude Code is answering your message…", force=True)
    assert _live(6109)["status"] == "Claude Code is answering your message…"
    # The answer is saved first, then live status clears (poll on idle finds the row).
    saver = _Saver()
    wait.bind(saver, {})
    wait.followup("frames rendered")
    assert saver.texts() == ["frames rendered"]
    assert _live(6109)["active"] is False and wait.phase == "waiting"
    # Back to waiting on the remaining task: card label, no live status.
    wait.waiting("Waiting on: encode video")
    assert _status(wait)["label"] == "Waiting on: encode video"
    assert _live(6109)["active"] is False
    # A newer real turn's status is never cleared by the detached run.
    wait.working("tool 2: Read", force=True)
    set_live_status("6109", "Cursor working…", turn=7)
    wait.finish("done", "Background work finished")
    assert _live(6109)["status"] == "Cursor working…" and _live(6109)["followup"] is False
    assert _status(wait)["state"] == "done"
    assert bw.detached_wait(6109) is None


def test_stream_lane_steers_into_detached_wait_without_running(monkeypatch):
    from api import chat_delivery
    from api.chat_coordinator import AgentSelection, PreparedAgentTurn, StreamTurnIO, submit_agent_stream_turn

    wait = bw.BackgroundWait("6104", "claude")
    wait.start(["watch CI"], pid=None)
    sent = []

    def _send(text):
        sent.append(text)
        fut = concurrent.futures.Future()
        fut.set_result((True, None))
        return fut

    token = steer.register(6104, "claude", _send)
    rows = []
    io = StreamTurnIO(
        run_harness=lambda *a, **k: pytest.fail("must steer, not run"),
        run_router=lambda **k: None, persist_user=lambda: None, make_saver=lambda: None,
        notify_mobile=None, format_shortcut=lambda *a: {}, persist_steered_user=rows.append,
    )
    events = list(submit_agent_stream_turn(
        PreparedAgentTurn(message="/claude status?", session_id=6104), io=io, delivery=chat_delivery,
        selection=AgentSelection(kind="harness", agent_id="claude", prompt="status?"),
    ))
    assert events[0][0] == "shortcut" and events[0][1]["no_reply"] is True
    assert sent == ["status?"] and rows == [{"steered": True, "steered_agent": "claude"}]
    steer.unregister(6104, token)
    wait.finish("done", "Background work finished")


def test_harness_turn_runs_normally_without_a_detached_wait():
    from api import chat_delivery
    from api.chat_coordinator import AgentSelection, AgentTurnIO, PreparedAgentTurn, submit_agent_turn

    ran = []
    io = AgentTurnIO(
        run_harness=lambda *a, **k: ran.append(a) or {"success": True, "response": "ran"},
        run_router=lambda **k: None, persist_user=lambda: None, make_saver=lambda: None,
        should_save=lambda b: False, notify_mobile=None, format_shortcut=lambda *a: {},
        persist_steered_user=lambda m: pytest.fail("nothing to steer into"),
    )
    out = submit_agent_turn(
        PreparedAgentTurn(message="/claude hi", session_id=6105), io=io, delivery=chat_delivery,
        claim=True, selection=AgentSelection(kind="harness", agent_id="claude", prompt="hi"),
    )
    assert out.body["response"] == "ran" and len(ran) == 1


@posix_only
def test_stop_kills_the_detached_run(tmp_path, monkeypatch):
    from api.chat_run_registry import cancel_session_runs, has_live_process

    res, wait, _ = _start(tmp_path, monkeypatch, "steer", 6106)
    saver = _Saver()
    bw.bind_saver(wait.job_id, saver, {})
    assert has_live_process("6106")
    pid = _status(wait)["pid"]
    cancel_session_runs("6106")
    assert _until(lambda: _status(wait).get("state") == "failed")
    assert _status(wait)["label"] == "Stopped"
    assert _until(lambda: steer.active_agent(6106) is None)
    assert _until(lambda: _gone(pid))
    assert saver.bodies == [], "a stopped run posts no bubble"


@posix_only
def test_card_stop_kills_the_detached_run(tmp_path, monkeypatch):
    from api.job_watch import cancel_job

    res, wait, _ = _start(tmp_path, monkeypatch, "steer", 6107)
    saver = _Saver()
    bw.bind_saver(wait.job_id, saver, {})
    pid = _status(wait)["pid"]
    assert cancel_job(wait.job_id)["killed"] == 1
    assert _until(lambda: _gone(pid))
    assert _until(lambda: bw.detached_wait(6107) is None)
    status = _status(wait)
    assert status["state"] == "failed" and status.get("cancelled") is True
    assert saver.bodies == []


@posix_only
def test_wait_cap_reaps_and_posts_the_abandoned_notice(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_BACKGROUND_WAIT_SEC", "1")
    monkeypatch.setenv("CLAUDE_BACKGROUND_EXIT_GRACE_SEC", "0.5")
    res, wait, took = _start(tmp_path, monkeypatch, "ignores_eof", 6108)
    assert took < 10 and res["output"].startswith("started the watcher")
    saver = _Saver()
    bw.bind_saver(wait.job_id, saver, {})
    assert _until(lambda: _status(wait).get("state") == "failed", timeout=20)
    assert _status(wait)["label"].startswith("Stopped waiting after")
    assert _until(lambda: len(saver.bodies) == 1)
    notice = saver.bodies[0]["response"]
    assert "Stopped waiting for background work" in notice and "- watch CI" in notice
    # The fake task has no process of its own, so it really did end with the turn.
    assert "This ended with the turn" in notice and "still running" not in notice
    assert steer.active_agent(6108) is None


@posix_only
def test_wait_cap_hands_running_work_off_and_the_chat_hears_when_it_ends(tmp_path, monkeypatch):
    """CH-002006: the cap must spare the still-running task and tell the chat when it ends."""
    from api.agent_harness import background_handoff as bh
    from api.chat_ops import store

    launched = []
    monkeypatch.setattr(bh, "_spawn_watcher", lambda path, sid: launched.append((path, sid)))
    monkeypatch.setenv("CLAUDE_BACKGROUND_WAIT_SEC", "1")
    monkeypatch.setenv("CLAUDE_BACKGROUND_EXIT_GRACE_SEC", "0.5")
    monkeypatch.setenv("FAKE_TASK_OUT", str(tmp_path / "b7.output"))
    monkeypatch.setenv("FAKE_TASK_SEC", "4")
    res, wait, took = _start(tmp_path, monkeypatch, "real_task", 6111)
    assert took < 10 and res["output"].startswith("started the blender job")
    saver = _Saver()
    bw.bind_saver(wait.job_id, saver, {})
    assert _until(lambda: _status(wait).get("state") in ("done", "failed"), timeout=20)
    status = _status(wait)
    assert status["state"] == "done" and "still running" in status["label"]
    assert _until(lambda: len(saver.bodies) == 1)
    notice = saver.bodies[0]["response"]
    assert "still running; this chat will be told when it finishes" in notice and "- blender job" in notice
    assert "ended with this turn" not in notice

    assert len(launched) == 1 and launched[0][1] == 6111
    record_path = launched[0][0]
    task = bh.json.loads(record_path.read_text())["tasks"][0]
    assert task["task_id"] == "b7" and task["output_file"] == str(tmp_path / "b7.output")
    assert bh.running(task), "the reap must spare the handed-off task"
    assert bh.check(record_path) is False  # still running: nothing posted yet
    assert not [o for o in store.listing(6111) if o["kind"] == "notice"]

    assert _until(lambda: not bh.running(task), timeout=15)
    assert bh.check(record_path) is True and not record_path.exists()
    ops = [o for o in store.listing(6111) if o["kind"] == "notice"]
    assert len(ops) == 1 and ops[0]["target"] == 6111 and ops[0]["state"] == "pending"
    text = ops[0]["payload"]["text"]
    assert "Background work from an earlier turn finished" in text
    assert "`blender job` (task b7)" in text and "blend saved" in text and str(tmp_path / "b7.output") in text


@posix_only
def test_follow_up_before_bind_is_held_until_the_first_reply_is_saved(tmp_path, monkeypatch):
    res, wait, _ = _start(tmp_path, monkeypatch, "finishes", 6109)
    assert _until(lambda: _status(wait).get("state") == "done")
    assert bw._active.get(wait.job_id) is wait  # held: nothing bound yet
    saver = _Saver()
    assert bw.bind_saver(wait.job_id, saver, {})
    assert saver.texts() == ["CI is green"]
    assert wait.job_id not in bw._active


@posix_only
def test_without_a_background_wait_the_follow_up_merges_into_one_reply(tmp_path, monkeypatch):
    res, wait, _ = _start(tmp_path, monkeypatch, "finishes", 6110, background=False)
    assert res["output"] == "started the watcher\n\nCI is green"
    assert "background_job" not in res


def test_off_switch_env_and_setting(monkeypatch):
    import managers.settings_manager as sm

    cfg = {}

    class _SM:
        def get_setting(self, key, default=None):
            return cfg.get(key, default)

    monkeypatch.setattr(sm, "get_settings_manager", lambda: _SM())
    monkeypatch.delenv("CUTTLE_BACKGROUND_DETACH", raising=False)
    assert bw.detach_enabled("claude") is True
    assert bw.detach_enabled("codex") is False
    cfg["agent_background_detach"] = {"claude": False}
    assert bw.detach_enabled("claude") is False
    cfg.clear()
    monkeypatch.setenv("CUTTLE_BACKGROUND_DETACH", "0")
    assert bw.detach_enabled("claude") is False


@pytest.mark.parametrize("enabled", [True, False])
def test_adapter_passes_a_background_wait_only_when_enabled(monkeypatch, tmp_path, enabled):
    import scripts.utilities.claude_cli_tool as mod
    from api.agent_harness.agents.claude.adapter import Adapter

    seen = {}

    async def _fake(self, prompt, **kw):
        seen.update(kw)
        return {"success": True, "output": "ok", "usage": {}, "claude_session_id": None,
                "background_job": "claude-bg-x" if kw.get("background") else None}

    monkeypatch.setattr(mod.ClaudeCliTool, "execute_prompt", _fake)
    monkeypatch.setattr("api.agent_harness.steer.steer_enabled", lambda _a: True)
    monkeypatch.setattr(bw, "detach_enabled", lambda _a: enabled)
    out = asyncio.run(Adapter().execute("hi", cwd=str(tmp_path), resume=None, model=None,
                                        chat_session_id="6111"))
    assert isinstance(seen.get("background"), bw.BackgroundWait) is enabled
    assert (out.meta.get("background_job") == "claude-bg-x") is enabled
