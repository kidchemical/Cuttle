#!/usr/bin/env python3
"""Real end-to-end check of execution nodes: a live agent CLI turn on a node.

Deliberate and opt-in: it spends real model tokens (a few short turns on a
cheap model). It never touches the running Cuttle: host state lives in a
throwaway Cuttle home, the coordinator is a private loopback HTTP server, and
the node is a second OS process with its own home that pairs with a device
bearer, claims the ``agent_turn`` job over HTTP and runs the vendor CLI with
this user's own CLI login.

    PYTHONPATH=src .venv/bin/python src/scripts/exec_node_smoke.py [--agent claude] [--model haiku]

What it proves, through the real harness kernel (context compile → remote
adapter → mesh job → relay → reply):
  1. a turn runs in the node process, not the host's (a marker only the node has);
  2. status lines stream back while it runs;
  3. the next turn resumes the node's CLI session;
  4. Stop reaches the node's CLI;
  5. switching the chat back to the host carries the conversation over.
"""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
MARK = f"node-mark-{os.getpid()}"

NODE_MAIN = """
import os, sys, threading
sys.path.insert(0, os.environ["SMOKE_SRC"])
from api.device_workers.worker_loop import run_remote_worker_loop
run_remote_worker_loop(should_continue=lambda: True)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", default="claude")
    ap.add_argument("--model", default="haiku", help="cheap model id for the turns")
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    args = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="cuttle-exec-node-smoke-"))
    host_home, node_home = root / "host-home", root / "node-home"
    host_project, node_work = root / "host-project", root / "node-work"
    for path in (host_home, node_home, host_project, node_work):
        path.mkdir(parents=True)
    # Isolate from whatever launched this (a live Cuttle turn exports its own state).
    for key in [k for k in os.environ if k.startswith(("CUTTLE_", "CLAUDE"))]:
        os.environ.pop(key)
    os.environ["CUTTLE_HOME"] = str(host_home)
    os.environ["CUTTLE_DEVICE_WORKER_ID"] = "smoke-host"
    os.environ["CHAT_TITLE_DISABLED"] = "1"
    sys.path.insert(0, str(SRC))

    from flask import Flask
    from werkzeug.serving import make_server

    from api import chat_delivery
    from api.agent_harness.kernel import run_agent_web_command
    from api.auth_db import get_auth_db
    from api.device_workers.routes import workers_bp
    from api.device_workers.store import get_store
    from api.exec_nodes import service, store
    from api.experimental import flags

    flags.set_enabled("exec_nodes", True)
    mesh = get_store()
    token = mesh.enroll_device(worker_id="smoke-node", hostname="SmokeNode")["token"]

    app = Flask("exec-node-smoke")
    app.register_blueprint(workers_bp)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    os.environ["CUTTLE_INTERNAL_API_BASE"] = base  # nothing here may reach a running Cuttle

    node_env = {
        **os.environ,
        "CUTTLE_HOME": str(node_home),
        "SMOKE_SRC": str(SRC),
        "SMOKE_NODE_MARK": MARK,  # only the node process (and the CLI it starts) has this
        "CUTTLE_DEVICE_WORKERS_ENABLED": "1",
        "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL": base,
        "CUTTLE_DEVICE_WORKER_TOKEN": token,
        "CUTTLE_DEVICE_WORKER_ID": "smoke-node",
        "CUTTLE_DEVICE_WORKERS_POLL_SECONDS": "2",
        "CUTTLE_NODE_AGENT_EXEC": "1",
    }
    node_log = open(root / "node.log", "w")
    node_proc = subprocess.Popen([sys.executable, "-c", NODE_MAIN], env=node_env, cwd=str(node_work),
                                 stdout=node_log, stderr=subprocess.STDOUT)
    failures: list = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
        if not ok:
            failures.append(name)

    try:
        print(f"host home {host_home}\nnode pid {node_proc.pid}, coordinator {base}", flush=True)
        deadline = time.time() + 40
        info = None
        while time.time() < deadline:
            info = service.node("smoke-node", args.agent)
            if info and info["state"] != "unavailable":
                break
            time.sleep(1)
        check("node registered with agent execution on", bool(info and info["state"] == "online"),
              f"{(info or {}).get('state')} {(info or {}).get('reason')} agents={(info or {}).get('agents')}")
        if failures:
            return 1

        db = get_auth_db()
        user = db.create_user("smoke@local", "Smoke", "local", password="x")
        sid = db.create_chat_session(user, "exec node smoke")
        service.set_binding(sid, "smoke-node", str(node_work))

        def turn(prompt: str, label: str, cancel_after: float = 0.0) -> tuple:
            statuses: queue.Queue = queue.Queue()
            db.add_message(sid, "user", f"/{args.agent} {prompt}")
            if cancel_after:
                from api.chat_run_registry import cancel_session_runs

                threading.Timer(cancel_after, lambda: cancel_session_runs(sid)).start()
            started = time.time()
            chat_delivery.try_begin(sid)  # as the chat route does: a new turn clears the last Stop
            try:
                body = run_agent_web_command(args.agent, prompt, sid, status_queue=statuses,
                                             project_path=str(host_project), model_override=args.model)
            finally:
                chat_delivery.end(sid, publish=False)
            seen = [statuses.get_nowait() for _ in range(statuses.qsize())]
            lines = [str(item[1]) for item in seen if isinstance(item, tuple) and item and item[0] == "status"]
            reply = str(body.get("response") or "")
            if body.get("type", "").endswith("_command") and not cancel_after:
                db.add_message(sid, "assistant", reply)
            print(f"\n== {label} ({time.time() - started:.1f}s, {len(lines)} status lines) ==\n{reply[:600]}", flush=True)
            return body, reply, lines

        body, reply, lines = turn(
            "Run the shell command `printf %s \"$SMOKE_NODE_MARK\" > where.txt` in the current directory, "
            "then reply with exactly the contents of where.txt and nothing else.", "turn 1 on the node")
        written = (node_work / "where.txt").read_text() if (node_work / "where.txt").exists() else ""
        check("CLI ran in the node process", written == MARK, f"where.txt={written!r}")
        check("reply came back to the host chat", MARK in reply)
        check("status streamed from the node",
              any(s.startswith("Running on ") for s in lines) and len(lines) >= 3, f"{len(lines)} lines: {lines[:6]}")
        check("host project untouched", not (host_project / "where.txt").exists())
        resume = store.load_resume(str(sid), args.agent, "smoke-node", str(host_project))
        check("node CLI session recorded on the host", bool(resume), str(resume))
        check("no prompt or reply left in the mesh job",
              all(MARK not in str(j) for j in mesh.list_jobs(limit=20)) and store.running_turns() == {})

        body, reply, _ = turn("What exact text did you write to where.txt? Reply with only that text.",
                              "turn 2 resumes on the node")
        check("resumed the same node session", MARK in reply)

        # One foreground wait and one detached child: Stop must end both.
        started = time.time()
        body, reply, _ = turn(
            "Run this exact shell command in the foreground and wait for it to finish (it is a deliberate "
            "long wait for a test; allow a 10 minute timeout): "
            "`(python3 -c 'import time; time.sleep(296)' &) ; python3 -c 'import time; time.sleep(297)'` "
            "Then say done.", "turn 3 stopped mid-run", cancel_after=25.0)
        took = time.time() - started
        check("Stop ended the turn mid-run", 24 <= took < 90, f"{took:.0f}s")
        time.sleep(4)
        import psutil

        left = [p.pid for p in psutil.process_iter(["cmdline"])
                if (p.info.get("cmdline") or [])[-1:] in (["import time; time.sleep(296)"], ["import time; time.sleep(297)"])]
        check("Stop left nothing running on the node", not left, f"pids {left}")

        service.set_binding(sid, "host")
        body, reply, _ = turn("Earlier in this conversation a text was written to where.txt. "
                              "Reply with only that text.", "turn 4 back on the host")
        check("conversation carried over to the host CLI", MARK in reply)
        check("host turn did not use the node", store.running_turns() == {} and
              mesh.list_jobs(limit=1)[0]["type"] == "agent_turn" and len(mesh.list_jobs(limit=20)) == 3)
    finally:
        node_proc.terminate()
        try:
            node_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            node_proc.kill()
        node_log.close()
        server.shutdown()
        if failures:
            print(f"\nnode log tail:\n{(root / 'node.log').read_text()[-3000:]}")
        if args.keep or failures:
            print(f"kept {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    print("\nRESULT: " + ("FAILED " + ", ".join(failures) if failures else "all checks passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
