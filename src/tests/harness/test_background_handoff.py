"""Background work a turn stopped waiting on (``api.agent_harness.background_handoff``).

The wait cap must not kill still-running work (``kill_process_tree`` ``keep``)
and the chat must hear when it ends (a Cuttle ``notice`` turn on the
``api.chat_ops`` outbox). The end-to-end path through the Claude CLI tool is in
``test_claude_background_detach.py``.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.agent_harness import background_handoff as bh  # noqa: E402
from api.chat_ops import store  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="uses /proc and POSIX sessions")


def _dead_proc():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return {"pid": p.pid, "create_time": time.time() - 5}


def _self_proc():
    import psutil

    return {"pid": os.getpid(), "create_time": psutil.Process().create_time()}


def _record(tmp_path, procs, *, created_ts=None, output="", sid=7101):
    path = tmp_path / "rec.json"
    path.write_text(json.dumps({
        "id": "claude-7101-abc", "chat_session_id": sid, "agent": "claude", "waited_sec": 3000,
        "created_ts": created_ts or time.time(),
        "tasks": [{"task_id": "b9", "label": "render", "output_file": output, "procs": procs,
                   "handback": "", "notified": False}],
    }))
    return path


def _notices(sid):
    return [o for o in store.listing(sid) if o["kind"] == "notice"]


def test_output_paths_read_the_run_in_background_tool_result():
    line = json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content":
        "Command running in background with ID: b0bxi2w00. Output is being written to: "
        "/tmp/claude-1000/-mnt-x/ed8b/tasks/b0bxi2w00.output. You will be notified when it completes."}]}})
    assert bh.output_paths(line.encode()) == {"b0bxi2w00": "/tmp/claude-1000/-mnt-x/ed8b/tasks/b0bxi2w00.output"}
    assert bh.output_paths(b'{"type": "assistant"}') == {}


def test_finished_task_posts_one_notice_with_its_output_tail(tmp_path):
    out = tmp_path / "b9.output"
    out.write_text("frame 1\nframe 2\n\x1b[32mall done\x1b[0m\n")
    path = _record(tmp_path, [_dead_proc()], output=str(out), sid=7101)
    assert bh.check(path) is True and not path.exists()
    ops = _notices(7101)
    assert len(ops) == 1 and ops[0]["actor"] == ops[0]["target"] == 7101
    assert ops[0]["payload"]["label"] == "Background work finished"
    text = ops[0]["payload"]["text"]
    assert "stopped waiting on it after 50 min" in text and "`render` (task b9)" in text
    assert "all done" in text and "\x1b[" not in text and str(out) in text


def test_running_task_is_not_reported_until_it_ends(tmp_path):
    path = _record(tmp_path, [_self_proc()], sid=7102)
    assert bh.check(path) is False and path.exists()
    assert _notices(7102) == []


def test_watch_gives_up_after_the_cap_and_says_it_still_runs(tmp_path):
    path = _record(tmp_path, [_self_proc()], created_ts=time.time() - bh.MAX_WATCH_SEC - 60, sid=7103)
    assert bh.check(path) is True and not path.exists()
    (op,) = _notices(7103)
    assert "stopped watching" in op["payload"]["text"] and "still running" in op["payload"]["text"]


def test_start_skips_subagent_runs_and_finished_work(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(bh, "_spawn_watcher", lambda path, sid: launched.append((path, sid)))
    sub = {"task_id": "s1", "label": "spawn", "procs": [_self_proc()], "handback": "subagents"}
    done = {"task_id": "d1", "label": "done", "procs": [_dead_proc()], "handback": ""}
    assert bh.start("7104", [sub, done], waited_sec=3000) is None
    assert launched == []
    live = {"task_id": "l1", "label": "live", "output_file": "", "procs": [_self_proc()], "handback": ""}
    path = bh.start("CH-007104", [sub, live], waited_sec=3000)
    assert launched == [(path, 7104)]
    rec = json.loads(path.read_text())
    assert [t["task_id"] for t in rec["tasks"]] == ["l1"] and rec["chat_session_id"] == 7104
    path.unlink()


@posix_only
def test_collect_finds_the_process_writing_the_task_file_and_flags_subagents(tmp_path):
    out = tmp_path / "t.output"
    script = (
        "import subprocess, sys, time\n"
        f"fh = open({str(out)!r}, 'w')\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)', 'api.subagents', 'spawn', '--wait'],"
        " stdout=fh, stderr=fh)\n"
        "print(c.pid, flush=True)\n"
        "time.sleep(30)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        child_pid = int(parent.stdout.readline())
        (entry,) = bh.collect(parent.pid, [{"task_id": "t", "description": "x"}], {"t": str(out)}, None,
                              lambda t: t["description"])
        assert [p["pid"] for p in entry["procs"]] == [child_pid]
        assert entry["handback"] == "subagents" and bh.running(entry)
    finally:
        from api.chat_run_registry import kill_process_tree

        kill_process_tree(parent)


def _alive(pid):
    """``/proc`` probe: signalling a reparented pid trips the test kill guard."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] not in ("Z", "X")
    except OSError:
        return False


def _until_gone(pid, timeout):
    deadline = time.time() + timeout
    while _alive(pid):
        if time.time() > deadline:
            return False
        time.sleep(0.05)
    return True


@posix_only
def test_kill_process_tree_spares_kept_pids():
    """The wait-cap reap kills the CLI but not the work handed off to outlive it.

    The root ignores SIGTERM (as a CLI busy shutting down does), so the sweep
    runs while the children are still its descendants and must skip ``keep``.
    """
    from api.chat_run_registry import kill_process_tree

    script = (
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])\n"
        "d = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(c.pid, d.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    kept, other = (int(x) for x in parent.stdout.readline().split())
    kill_process_tree(parent, keep=[kept])
    assert parent.poll() is not None
    assert _until_gone(other, 5), "unkept descendants are still swept"
    assert _alive(kept), "the kept pid must survive the tree kill"
    # The kept child then finishes on its own (it is no longer ours to signal).


def test_notice_turn_renders_as_cuttle_with_its_label():
    from api.chat_metadata import chat_op_metadata

    op = bh.post_notice(7105, "Background work from an earlier turn finished.", "Background work finished")
    meta = chat_op_metadata({"chat_op_id": op["id"]}, session_id="db_session_7105")
    assert meta["speaker"] == "Cuttle" and meta["parent_label"] == "Background work finished"
    assert chat_op_metadata({"chat_op_id": op["id"]}, session_id="9999") == {"chat_op_id": op["id"]}
