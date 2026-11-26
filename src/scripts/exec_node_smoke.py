#!/usr/bin/env python3
"""Real end-to-end check of execution nodes: a live agent CLI turn on a node.

Deliberate and opt-in: it spends real model tokens (a few short turns on a
cheap model). It never touches the running Cuttle: host state lives in a
throwaway Cuttle home, the coordinator is a private loopback HTTP server, and
the node is a second OS process with its own home that pairs with a device
bearer, claims the ``agent_turn`` job over HTTP and runs the vendor CLI with
this user's own CLI login.

    PYTHONPATH=src .venv/bin/python src/scripts/exec_node_smoke.py [--agent claude] [--model haiku]

``--node-ssh HOST`` runs the node on another machine instead (Windows OpenSSH,
``cmd.exe`` shell, a Cuttle checkout at ``--node-repo``). The node reaches the
throwaway coordinator through an SSH reverse tunnel, so nothing listens on the
LAN and the throwaway device token only travels inside SSH.

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
import json
import os
import random
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

# Turn prompts per node OS: the agent's own shell is bash on Linux, bash or
# PowerShell on Windows; both run these. 296 s is the detached child, 297 s the
# foreground wait: Stop must end both.
POSIX_STOP = ("`(python3 -c 'import time; time.sleep(296)' &) ; python3 -c 'import time; time.sleep(297)'`")
WINDOWS_STOP = ("`powershell -NoProfile -Command \"Start-Process powershell -WindowStyle Hidden -ArgumentList "
                "'-NoProfile','-Command','Start-Sleep -Seconds 296'; Start-Sleep -Seconds 297\"`")


class LocalNode:
    """The node as a second process on this machine."""

    windows = os.name == "nt"

    def __init__(self, root: Path) -> None:
        self.work = root / "node-work"
        self.home = root / "node-home"
        for path in (self.work, self.home):
            path.mkdir(parents=True)
        self.log_path = root / "node.log"
        self.proc = None

    def start(self, env: dict, base: str) -> None:
        self.log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-c", NODE_MAIN],
                                     env={**os.environ, **env, "CUTTLE_HOME": str(self.home),
                                          "SMOKE_SRC": str(SRC), "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL": base},
                                     cwd=str(self.work), stdout=self.log, stderr=subprocess.STDOUT)

    def read(self, name: str) -> str:
        path = self.work / name
        return path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""

    def leftovers(self) -> list:
        import psutil

        return [p.pid for p in psutil.process_iter(["cmdline"])
                if any("time.sleep(29" in part for part in (p.info.get("cmdline") or []))]

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.log.close()

    def log_tail(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if self.log_path.exists() else ""


class SshNode:
    """The node on another machine over the user's OpenSSH (Windows target)."""

    windows = True

    def __init__(self, root: Path, host: str, repo: str) -> None:
        self.host, self.repo = host, repo.rstrip("\\")
        tag = f"cuttle-exec-node-smoke-{os.getpid()}"
        temp = self.ssh("echo %TEMP%").strip().splitlines()[-1]
        self.dir = f"{temp}\\{tag}"
        self.work, self.home = f"{self.dir}\\work", f"{self.dir}\\home"
        self.ssh(f'mkdir "{self.work}" "{self.home}"')
        self.log_path = root / "node.log"
        self.proc = None

    def ssh(self, command: str, timeout: float = 60) -> str:
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.host, command],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=timeout)
        return out.stdout

    def start(self, env: dict, base: str) -> None:
        port = int(base.rsplit(":", 1)[1])
        remote_port = random.randint(18100, 18999)
        node_env = {**env, "CUTTLE_HOME": self.home, "SMOKE_SRC": f"{self.repo}\\src",
                    "CUTTLE_DEVICE_WORKERS_COORDINATOR_URL": f"http://127.0.0.1:{remote_port}"}
        # Env and token go over SSH stdin, never onto a command line.
        script = (f"import os, json, sys\nsys.stdout.reconfigure(encoding='utf-8', errors='replace')\n"
                  f"os.environ.update(json.loads({json.dumps(json.dumps(node_env))}))\n"
                  f"os.chdir({self.work!r})\nopen('node.pid', 'w').write(str(os.getpid()))\n" + NODE_MAIN)
        self.log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             "-R", f"{remote_port}:127.0.0.1:{port}", self.host, f'"{self.repo}\\.venv\\Scripts\\python.exe" -u -'],
            stdin=subprocess.PIPE, stdout=self.log, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        self.proc.stdin.write(script)
        self.proc.stdin.close()

    def read(self, name: str) -> str:
        return self.ssh(f'type "{self.work}\\{name}" 2>nul').strip()

    def leftovers(self) -> list:
        out = self.ssh('powershell -NoProfile -Command "Get-CimInstance Win32_Process | '
                       "? { $_.CommandLine -match 'Start-Sleep -Seconds 29[67]' -and $_.CommandLine -notmatch 'CimInstance' } | "
                       '% { $_.ProcessId }"')
        return [int(x) for x in out.split() if x.isdigit()]

    def stop(self) -> None:
        pid = self.read("node.pid")
        if pid.isdigit():
            self.ssh(f"taskkill /pid {pid} /t /f >nul 2>&1")
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.log.close()
        self.ssh(f'rmdir /s /q "{self.dir}"')

    def log_tail(self) -> str:
        return self.log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if self.log_path.exists() else ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agent", default="claude")
    ap.add_argument("--model", default="haiku", help="cheap model id for the turns")
    ap.add_argument("--keep", action="store_true", help="keep the temp directory")
    ap.add_argument("--node-ssh", default="", help="run the node on this SSH host (Windows) instead of locally")
    ap.add_argument("--node-repo", default=r"C:\Users\me\Dev\Cuttle", help="Cuttle checkout on the SSH node")
    args = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="cuttle-exec-node-smoke-"))
    host_home, host_project = root / "host-home", root / "host-project"
    for path in (host_home, host_project):
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

    node = SshNode(root, args.node_ssh, args.node_repo) if args.node_ssh else LocalNode(root)
    node_env = {
        "SMOKE_NODE_MARK": MARK,  # only the node process (and the CLI it starts) has this
        "CUTTLE_DEVICE_WORKERS_ENABLED": "1",
        "CUTTLE_DEVICE_WORKER_TOKEN": token,
        "CUTTLE_DEVICE_WORKER_ID": "smoke-node",
        "CUTTLE_DEVICE_WORKERS_POLL_SECONDS": "2",
        "CUTTLE_NODE_AGENT_EXEC": "1",
    }
    node.start(node_env, base)
    failures: list = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
        if not ok:
            failures.append(name)

    try:
        print(f"host home {host_home}\nnode {args.node_ssh or 'local'} work {node.work}, coordinator {base}", flush=True)
        deadline = time.time() + 60
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
        service.set_binding(sid, "smoke-node", str(node.work))

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
            "With one shell command, write the value of the environment variable SMOKE_NODE_MARK to a file "
            "named where.txt in the current directory, then reply with exactly the contents of where.txt "
            "and nothing else.", "turn 1 on the node")
        written = node.read("where.txt")
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
            + (WINDOWS_STOP if node.windows else POSIX_STOP) + " Then say done.",
            "turn 3 stopped mid-run", cancel_after=25.0)
        took = time.time() - started
        check("Stop ended the turn mid-run", 24 <= took < 90, f"{took:.0f}s")
        time.sleep(8)
        left = node.leftovers()
        check("Stop left nothing running on the node", not left, f"pids {left}")

        service.set_binding(sid, "host")
        body, reply, _ = turn("Earlier in this conversation a text was written to where.txt. "
                              "Reply with only that text.", "turn 4 back on the host")
        check("conversation carried over to the host CLI", MARK in reply)
        check("host turn did not use the node", store.running_turns() == {} and
              mesh.list_jobs(limit=1)[0]["type"] == "agent_turn" and len(mesh.list_jobs(limit=20)) == 3)
    finally:
        node.stop()
        server.shutdown()
        if failures:
            print(f"\nnode log tail:\n{node.log_tail()}")
        if args.keep or failures:
            print(f"kept {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    print("\nRESULT: " + ("FAILED " + ", ".join(failures) if failures else "all checks passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
