"""Processes a finished turn leaves running are leftovers, not busy work.

A `dotnet build` leaves VBCSCompiler alive and re-parented away from Flask.
It used to keep the chat "busy" and block a when-idle Flask restart, with no
way to see or stop it from Jobs.

The real-process scenarios run in a child interpreter: a re-parented daemon is
by definition outside pytest's tree, which the conftest kill guard (rightly)
refuses to signal or even probe.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2]

_PRELUDE = textwrap.dedent('''
    import json, os, subprocess, sys
    import api.flask_restart
    api.flask_restart.maybe_fire_when_idle = lambda: None
    from api import chat_run_registry as crr

    SID = "990001"
    SPAWNER = (
        "import subprocess, sys;"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL);"
        "print(c.pid, flush=True); sys.stdin.read()"
    )

    def turn_leaving_daemon(qid="q-leftover"):
        crr.begin_run(SID, qid)
        parent = subprocess.Popen([sys.executable, "-c", SPAWNER],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        crr.attach_process(SID, parent)
        child = int(parent.stdout.readline())
        out["busy_while_running"] = crr.has_live_process(SID)
        out["tracked"] = child in crr.session_tracked_pids(SID)
        parent.stdin.close()
        parent.wait(timeout=10)
        return child

    out = {}
    try:
''')

_EPILOGUE = textwrap.dedent('''
    finally:
        for pid in out.get("cleanup", []):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        print(json.dumps(out))
''')


def _scenario(body: str) -> dict:
    script = _PRELUDE + textwrap.indent(textwrap.dedent(body), "    ") + _EPILOGUE
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    res = subprocess.run([sys.executable, "-c", script], cwd=str(SRC), env=env,
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX re-parenting")


def test_finished_turn_detaches_survivor_into_leftovers():
    out = _scenario('''
        child = turn_leaving_daemon()
        out["cleanup"] = [child]
        out["busy_after_cli_exit"] = crr.has_live_process(SID)
        crr.end_run(SID, "q-leftover")
        out["busy_after_end"] = crr.has_live_process(SID)
        out["active"] = crr.active_run_session_ids()
        out["rows"] = crr.list_leftovers()
        out["child"] = child
    ''')
    assert out["busy_while_running"] and out["tracked"]
    # Until the turn ends, a surviving descendant still counts as its work.
    assert out["busy_after_cli_exit"] is True
    assert out["busy_after_end"] is False
    assert out["active"] == []
    assert [r["pid"] for r in out["rows"]] == [out["child"]]
    assert out["rows"][0]["session_id"] == "990001"
    assert "time.sleep(60)" in out["rows"][0]["cmdline"]


def test_terminate_leftover_kills_reparented_process():
    out = _scenario('''
        child = turn_leaving_daemon()
        out["cleanup"] = [child]
        crr.end_run(SID, "q-leftover")
        out["result"] = crr.terminate_leftover(child)
        out["dead"] = crr.wait_pid_dead(child, timeout=5)
        out["rows"] = crr.list_leftovers()
        try:
            crr.terminate_leftover(child)
            out["second"] = "ok"
        except LookupError:
            out["second"] = "lookup"
    ''')
    assert out["result"]["terminated"] >= 1
    assert out["dead"] is True
    assert out["rows"] == []
    assert out["second"] == "lookup"


def test_reused_pid_is_never_listed_or_killed():
    out = _scenario('''
        child = turn_leaving_daemon()
        out["cleanup"] = [child]
        crr.end_run(SID, "q-leftover")
        crr._leftovers[child]["create_time"] -= 1000  # identity no longer matches
        try:
            crr.terminate_leftover(child)
            out["kill"] = "ok"
        except LookupError:
            out["kill"] = "lookup"
        out["alive"] = crr.pid_alive(child)
        out["rows"] = crr.list_leftovers()
    ''')
    assert out["kill"] == "lookup"
    assert out["alive"] is True
    assert out["rows"] == []


def test_survivor_stays_busy_while_another_query_is_live():
    out = _scenario('''
        child = turn_leaving_daemon()
        out["cleanup"] = [child]
        # A second query on this chat is still live (no begin_run: its pre-kill
        # sweep would wait on the un-killable daemon).
        crr._runs[SID]["query_ids"].add("q-next")
        crr.end_run(SID, "q-leftover")
        out["rows"] = crr.list_leftovers()
    ''')
    assert out["rows"] == []


def test_jobs_lists_and_terminates_leftover(owner_session, monkeypatch):
    from flask import Flask

    from api import chat_run_registry as crr
    from api.jobs.routes import jobs_bp

    rows = [{"pid": 4242, "name": "VBCSCompiler", "cmdline": "dotnet VBCSCompiler.dll",
             "create_time": 1.0, "session_id": "1501", "detached_at": 2.0}]

    def fake_terminate(pid):
        hit = [r for r in rows if r["pid"] == int(pid)]
        if not hit:
            raise LookupError("gone")
        rows.remove(hit[0])
        return {"pid": int(pid), "name": hit[0]["name"], "terminated": 1}

    monkeypatch.setattr(crr, "list_leftovers", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(crr, "terminate_leftover", fake_terminate)
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(jobs_bp)
    client = owner_session.sign_in(app.test_client())

    body = client.get("/api/jobs/in-flight").get_json()
    assert all(j["kind"] != "leftover" for j in body["executing_jobs"])
    row = body["leftover_processes"][0]
    assert (row["kind"], row["id"], row["label"]) == ("leftover", "4242", "VBCSCompiler")
    assert row["actions"] == ["cancel"]
    assert "CH-001501" in row["meta"]["detail"]

    assert client.post("/api/jobs/leftover/4242/cancel").status_code == 200
    assert client.get("/api/jobs/in-flight").get_json()["leftover_processes"] == []
    assert client.post("/api/jobs/leftover/4242/cancel").status_code == 404
