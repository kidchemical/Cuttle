"""CLI lifetime contract for sub-agent child chats (real subprocesses).

Child turns run on daemon threads of the CLI process. B3: without a live
owner (``--wait``) the process exits immediately and rows stick at
pending/running forever. The CLI must therefore reject ``spawn``/``message``
without ``--wait`` before any row exists; a supervised ``--wait`` round that
hits its timeout must terminally cancel the unfinished work it owns and exit
nonzero instead of abandoning active rows; and the observation-only ``wait``
verb must never start stale pending work or cancel another owner's batch.

Execution is faked from test-owned fixture code only: each test writes a
``sitecustomize.py`` into a temporary ``PYTHONPATH`` entry that swaps the
existing ``turns.default_runner`` seam before the shipped CLI main runs.
Production ships no fake-execution mode. The real coordinator/runner path,
persistence, and cancellation APIs stay live; no vendor CLI, model, or
network is touched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

WT = Path(__file__).resolve().parents[3]
SRC = WT / "src"
STATE = WT / "temp"

_SITE_TMPL = """\
import json as _b3_json
import os as _b3_os
import sys as _b3_sys
import time as _b3_time


def _b3_refuse(reason):
    # Fail closed: site.py swallows ordinary exceptions raised here and
    # continues startup, so raising alone would fall through to the real
    # default_runner. os._exit aborts before CLI main ever runs.
    _b3_sys.stderr.write("b3 sitecustomize: refusing to run: %s\\n" % (reason,))
    _b3_sys.stderr.flush()
    _b3_os._exit(125)


def _b3_make_runner():
    mode = (_b3_os.environ.get("B3_FAKE_RUNNER") or "").strip()
    log_path = (_b3_os.environ.get("B3_FAKE_RUNNER_LOG") or "").strip()

    def _log(title):
        if not log_path:
            return
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(_b3_json.dumps({"title": title, "ts": _b3_time.time()}) + "\\n")
        except OSError:
            pass

    def _reply(prompt, session_id):
        _log(prompt)
        return {"success": True, "response": "[fake-runner] %s: %s" % (session_id, prompt)}

    def _call_kwargs(kwargs):
        # default_runner seam: prompt/session_id, not the ChildSpec.
        return (kwargs.get("prompt") or "", kwargs.get("session_id"))

    if mode == "1":
        def _runner(**kwargs):
            prompt, session_id = _call_kwargs(kwargs)
            return _reply(prompt, session_id)
        return _runner
    if mode.startswith("slow:"):
        delay = max(0.0, float(mode.split(":", 1)[1]))

        def _slow(**kwargs):
            _b3_time.sleep(delay)
            prompt, session_id = _call_kwargs(kwargs)
            return _reply(prompt, session_id)
        return _slow
    if mode.startswith("fail:"):
        msg = mode.split(":", 1)[1] or "fake failure"

        def _fail(**kwargs):
            return {"success": False, "error": msg, "response": ""}
        return _fail
    return None


try:
    if _b3_os.environ.get("B3_BREAK_IMPORT"):
        import api.subagents.nonexistent_bootstrap_probe  # noqa: F401
    else:
        import api.subagents.turns as _b3_turns
except Exception as _b3_exc:
    _b3_refuse("cannot import turns: %r" % (_b3_exc,))
_b3_mode = (_b3_os.environ.get("B3_FAKE_RUNNER") or "").strip()
_b3_runner = _b3_make_runner()
if not _b3_mode or _b3_runner is None:
    _b3_refuse("fake runner NOT activated (mode=%r)" % (_b3_mode,))
_b3_turns.default_runner = _b3_runner
"""


def _seed_db(db_path: Path) -> int:
    from api.auth_db import AuthDatabase

    db = AuthDatabase(db_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    return int(db.create_chat_session(owner, "B3 parent"))


@pytest.fixture()
def parent(tmp_path: Path):
    db_path = tmp_path / "cli.db"
    pid = _seed_db(db_path)
    site = tmp_path / "b3site"
    site.mkdir()
    # A template syntax error would otherwise fail open (site.py swallows
    # the import error and the real runner survives): prove it compiles in
    # the test process before any subprocess can run.
    compile(_SITE_TMPL, "sitecustomize.py", "exec")
    (site / "sitecustomize.py").write_text(_SITE_TMPL, encoding="utf-8")

    def _run_cli(*argv: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        for name in (
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "DEEPSEEK_API_KEY",
            "GEMINI_API_KEY",
            "META_API_KEY",
            "CURSOR_API_KEY",
            "CODEX_API_KEY",
            "OPENROUTER_API_KEY",
            "DISCORD_TOKEN",
        ):
            env.pop(name, None)
        env["PYTHONPATH"] = os.pathsep.join([str(site), str(SRC)])
        env["TMPDIR"] = str(STATE)
        # Default to the instant fake runner: no test subprocess may ever
        # reach a real vendor CLI by accident.
        env["B3_FAKE_RUNNER"] = "1"
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, "-m", "api.subagents", *argv],
            cwd=SRC,
            env=env,
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=120,
        )

    return db_path, pid, f"CH-{pid:06d}", _run_cli, site


def _spawn(parent, _run_cli, *extra: str, env_extra: dict | None = None):
    db_path, _pid, handle, _cli, _site = parent
    return _cli(
        "spawn", "--parent", handle, "--json", "--db", str(db_path), *extra,
        env_extra=env_extra,
    )


def _counts(db_path: Path) -> tuple[int, int, int]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        batches = con.execute("SELECT COUNT(*) FROM subagent_batches").fetchone()[0]
        try:
            children = con.execute("SELECT COUNT(*) FROM subagent_children").fetchone()[0]
        except Exception:
            children = 0
        subagent_sessions = con.execute(
            "SELECT COUNT(*) FROM chat_sessions WHERE origin = 'subagent'"
        ).fetchone()[0]
    finally:
        con.close()
    return int(batches), int(children), int(subagent_sessions)


def _child_states(db_path: Path) -> list[tuple[str, str]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT session_id, status FROM subagent_children ORDER BY sort_index"
        ).fetchall()
    finally:
        con.close()
    return [(str(sid), str(st)) for sid, st in rows]


def _messages(db_path: Path, session_id: int) -> list[tuple[str, str]]:
    from api.auth_db import AuthDatabase

    db = AuthDatabase(db_path)
    return [(str(m.get("role")), str(m.get("content") or "")) for m in db.get_messages(int(session_id))]


def _poll(fn, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.2)
    return False


def test_spawn_without_wait_rejected_before_rows(parent, tmp_path: Path):
    db_path, _pid, _handle, _cli, _site = parent
    proc = _spawn(
        parent, _cli, "--child", json.dumps({"title": "Ghost", "message": "hi?"}),
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--wait" in (proc.stdout + proc.stderr)
    batches, children, subagent_sessions = _counts(db_path)
    assert (batches, children) == (0, 0), "no batch/child rows may exist"
    assert subagent_sessions == 0, "no child chat session may be created"


def test_message_without_wait_rejected_before_side_effects(parent):
    db_path, _pid, _handle, _cli, _site = parent
    spawned = _spawn(
        parent, _cli, "--wait", "--lifetime", "conversational",
        "--child", json.dumps({"title": "Chatty", "message": "hello"}),
    )
    assert spawned.returncode == 0, spawned.stdout + spawned.stderr
    child = json.loads(spawned.stdout)["children"][0]
    before = _messages(db_path, child["session_id"])
    proc = _cli(
        "message", "--session", child["handle"], "--text", "sneaky follow-up",
        "--json", "--db", str(db_path),
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--wait" in (proc.stdout + proc.stderr)
    assert _messages(db_path, child["session_id"]) == before
    assert _counts(db_path)[1] == 1, "no extra child row may be created"


@pytest.mark.parametrize(
    "extra_env,marker",
    [
        ({"B3_FAKE_RUNNER": "bogus-mode"}, "NOT activated"),
        ({"B3_FAKE_RUNNER": "1", "B3_BREAK_IMPORT": "1"}, "cannot import turns"),
    ],
)
def test_bootstrap_failure_refuses_before_rows(parent, extra_env, marker):
    """Bootstrap must fail closed: unknown mode or broken patch target exits
    nonzero during startup (os._exit — site.py swallows raised exceptions)
    before CLI main, DB changes, or any execution."""
    db_path, _pid, handle, _cli, _site = parent
    proc = _cli(
        "spawn", "--parent", handle, "--json", "--db", str(db_path),
        "--child", json.dumps({"title": "Ghost", "message": "hi?"}),
        env_extra=extra_env,
    )
    assert proc.returncode == 125, proc.stdout + proc.stderr
    assert "refusing to run" in proc.stderr
    assert marker in proc.stderr
    assert _counts(db_path) == (0, 0, 0), "refused bootstrap must leave no rows"


def test_spawn_wait_completes_all_parallel(parent):
    db_path, _pid, _handle, _cli, _site = parent
    proc = _spawn(
        parent, _cli, "--wait", "--collect", "all",
        "--child", json.dumps({"title": "A", "message": "eggs?"}),
        "--child", json.dumps({"title": "B", "message": "spicy?"}),
        "--child", json.dumps({"title": "C", "message": "fast?"}),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True and payload["status"] == "done"
    assert [c["status"] for c in payload["children"]] == ["done"] * 3
    for child in payload["children"]:
        assert "[fake-runner]" in (child.get("result") or "")
        roles = [r for r, _ in _messages(db_path, child["session_id"])]
        assert "user" in roles and "assistant" in roles


def test_spawn_wait_serial_runs_in_child_order(parent, tmp_path: Path):
    db_path, _pid, _handle, _cli, _site = parent
    log = tmp_path / "fake-runs.jsonl"
    proc = _spawn(
        parent, _cli, "--wait", "--collect", "serial",
        "--child", json.dumps({"title": "One", "message": "1"}),
        "--child", json.dumps({"title": "Two", "message": "2"}),
        "--child", json.dumps({"title": "Three", "message": "3"}),
        env_extra={"B3_FAKE_RUNNER_LOG": str(log)},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["status"] == "done"
    order = [json.loads(line)["title"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert order == ["1", "2", "3"], "serial children must run in --child order"


def test_spawn_wait_first_collect_reaches_terminal(parent):
    db_path, _pid, _handle, _cli, _site = parent
    proc = _spawn(
        parent, _cli, "--wait", "--collect", "first",
        "--child", json.dumps({"title": "F1", "message": "a"}),
        "--child", json.dumps({"title": "F2", "message": "b"}),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] in ("done", "failed")
    terminal = {"done", "cancelled", "failed"}
    assert all(c["status"] in terminal for c in payload["children"])


def test_spawn_wait_failure_reaches_terminal_failed(parent):
    db_path, _pid, _handle, _cli, _site = parent
    proc = _spawn(
        parent, _cli, "--wait",
        "--child", json.dumps({"title": "Doomed", "message": "hi?"}),
        env_extra={"B3_FAKE_RUNNER": "fail:fake boom"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "failed"
    child = payload["children"][0]
    assert child["status"] == "failed"
    assert "fake boom" in (child.get("error") or "")


def test_message_wait_followup_round(parent):
    db_path, _pid, _handle, _cli, _site = parent
    spawned = _spawn(
        parent, _cli, "--wait", "--lifetime", "conversational",
        "--child", json.dumps({"title": "Chatty", "message": "hello"}),
    )
    assert spawned.returncode == 0, spawned.stdout + spawned.stderr
    child = json.loads(spawned.stdout)["children"][0]
    proc = _cli(
        "message", "--session", child["handle"], "--text", "and dessert?",
        "--wait", "--json", "--db", str(db_path),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    assert "dessert?" in (payload.get("response") or "")
    roles = [r for r, _ in _messages(db_path, child["session_id"])]
    assert roles.count("user") == 2 and roles.count("assistant") == 2


def test_wait_verb_on_finished_batch(parent):
    db_path, _pid, _handle, _cli, _site = parent
    spawned = _spawn(
        parent, _cli, "--wait",
        "--child", json.dumps({"title": "Q", "message": "hi?"}),
    )
    batch_id = json.loads(spawned.stdout)["id"]
    started = time.monotonic()
    proc = _cli("wait", batch_id, "--json", "--db", str(db_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "done"
    assert payload.get("timed_out") is False
    assert time.monotonic() - started < 20.0, "finished batch must not be re-polled"


def test_spawn_wait_timeout_cancels_own_work_and_exits_nonzero(parent):
    db_path, _pid, _handle, _cli, _site = parent
    started = time.monotonic()
    proc = _spawn(
        parent, _cli, "--wait", "--timeout", "3",
        "--child", json.dumps({"title": "Slow", "message": "hi?"}),
        env_extra={"B3_FAKE_RUNNER": "slow:30"},
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert elapsed < 25.0, "the wait must give up instead of supervising forever"
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload.get("timed_out") is True
    assert payload["status"] == "cancelled"
    assert [c["status"] for c in payload["children"]] == ["cancelled"]
    assert "timed out" in (payload.get("error") or "")
    # No late assistant: the cancelled turn never persisted a reply.
    sid = payload["children"][0]["session_id"]
    roles = [r for r, _ in _messages(db_path, sid)]
    assert "user" in roles and "assistant" not in roles


def test_wait_on_stale_pending_starts_nothing(parent):
    """Observation-only wait must not revive execution nobody owns."""
    from api.auth_db import AuthDatabase
    from api.subagents import store
    from api.subagents.types import ChildSpec

    db_path, pid, _handle, _cli, _site = parent
    db = AuthDatabase(db_path)
    user_id = int(db.get_chat_session_by_id(pid)["user_id"])
    batch = store.insert_batch(
        db, parent_session_id=pid, user_id=user_id,
        collect="serial", lifetime="one_shot",
    )
    sids = []
    for i, title in enumerate(("Stale One", "Stale Two")):
        sid = db.create_subagent_chat_session(
            user_id, parent_session_id=pid, session_name=title)
        sids.append(sid)
        store.insert_child(
            db, batch_id=batch.id, session_id=sid, sort_index=i,
            spec=ChildSpec(title=title, message=f"stale {i}", agent="cursor"),
            prompt=f"stale {i}",
        )
    proc = _cli("wait", batch.id, "--timeout", "4", "--json", "--db", str(db_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout).get("timed_out") is True
    assert _child_states(db_path) == [(str(s), "pending") for s in sids]
    for sid in sids:
        assert _messages(db_path, sid) == [], "stale pending work must not execute"


def test_cancel_running_batch_from_second_process(parent):
    db_path, pid, _handle, _cli, site = parent
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(site), str(SRC)])
    env["TMPDIR"] = str(STATE)
    env["B3_FAKE_RUNNER"] = "slow:30"
    child_spec = json.dumps({"title": "Slow", "message": "hi?"})
    worker = subprocess.Popen(
        [sys.executable, "-m", "api.subagents", "spawn",
         "--parent", f"CH-{pid:06d}", "--wait", "--timeout", "60",
         "--json", "--db", str(db_path), "--child", child_spec],
        cwd=SRC, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        assert _poll(
            lambda: any(st == "running" for _, st in _child_states(db_path)),
            timeout=20.0,
        ), "child turn must start in the supervised process"
        batch = _cli("status", "--parent", f"CH-{pid:06d}", "--json", "--db", str(db_path))
        batch_id = json.loads(batch.stdout)["batches"][0]["id"]
        cancel = _cli("cancel", "--batch", batch_id, "--json", "--db", str(db_path))
        assert cancel.returncode == 0, cancel.stdout + cancel.stderr
        out, _ = worker.communicate(timeout=60)
        payload = json.loads(out)
        assert payload["status"] == "cancelled"
        assert all(st == "cancelled" for _, st in _child_states(db_path))
        roles = [r for r, _ in _messages(db_path, int(payload["children"][0]["session_id"]))]
        assert "assistant" not in roles, "cancelled turn must persist no reply"
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=30)
