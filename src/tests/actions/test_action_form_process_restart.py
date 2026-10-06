"""Isolated interpreter / Flask-process restart recovery (not the live daemon)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SRC = REPO / "src"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = int(s.getsockname()[1])
    s.close()
    return port


_CHILD = r"""
import json, os, sys, time
from pathlib import Path

src = Path(os.environ["CUTTLE_SRC"])
sys.path.insert(0, str(src))
os.environ["CUTTLE_ACTION_HMAC_SECRET"] = os.environ["CUTTLE_ACTION_HMAC_SECRET"]

from api import auth_db as auth_db_mod
from api import project_actions as pa

db_path = Path(os.environ["CUTTLE_TEST_DB"])
auth_db_mod.DB_PATH = db_path
auth_db_mod._db_instance = None
db = auth_db_mod.AuthDatabase(db_path)
auth_db_mod._db_instance = db

pa._hmac_secret_cache = None

from api.web_chat_api import app
from werkzeug.serving import make_server

httpd = make_server("127.0.0.1", int(os.environ["CUTTLE_TEST_PORT"]), app)
httpd.serve_forever()
"""


@pytest.fixture()
def restart_env(tmp_path, monkeypatch):
    secret = "process-restart-hmac"
    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", secret)
    import api.project_actions as pa

    pa._hmac_secret_cache = None
    from api import auth_db as auth_db_mod
    from api.action_forms import clear_forms_for_tests, rewrite_action_forms
    from api.project_actions import clear_pending_for_tests, rewrite_cuttle_confirms

    db_path = tmp_path / "restart.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr("api.web_chat_api.get_auth_db", lambda: db)
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    clear_forms_for_tests()
    clear_pending_for_tests()
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "restart")
    token = db.create_auth_session(owner)
    return {
        "db": db,
        "db_path": db_path,
        "sid": sid,
        "token": token,
        "secret": secret,
        "rewrite_forms": rewrite_action_forms,
        "rewrite_confirms": rewrite_cuttle_confirms,
        "clear_forms": clear_forms_for_tests,
        "clear_pending": clear_pending_for_tests,
        "tmp": tmp_path,
    }


def _start_child_flask(env: dict, port: int) -> subprocess.Popen:
    child_env = os.environ.copy()
    child_env["CUTTLE_SRC"] = str(SRC)
    child_env["CUTTLE_TEST_DB"] = str(env["db_path"])
    child_env["CUTTLE_TEST_PORT"] = str(port)
    child_env["CUTTLE_ACTION_HMAC_SECRET"] = env["secret"]
    child_env["PYTHONPATH"] = str(SRC) + os.pathsep + child_env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        cwd=str(REPO),
        env=child_env,
        stdout=subprocess.DEVNULL,
        # A file, not a pipe: nobody drains it while we poll, and the child
        # blocks once Windows' small pipe buffer fills with import logging.
        stderr=open(env["tmp"] / "child-flask.err", "wb"),
    )


def _wait_http(port: int, timeout: float = 45.0, proc=None, log: Path = None) -> None:
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            break  # the child died; report its stderr instead of waiting out
        try:
            # Windows runners answer health in ~1s; the deadline above bounds it.
            urllib.request.urlopen(url, timeout=5)
            return
        except Exception as exc:
            last = exc
            time.sleep(0.1)
    detail = ""
    if log is not None and log.is_file():
        detail = "\n" + log.read_bytes().decode("utf-8", "replace")[-3000:]
    raise AssertionError(f"isolated Flask did not listen on {port}: {last}{detail}")


def _post_json(port: int, path: str, cookie: str, payload: dict):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Cookie": f"session_token={cookie}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return resp.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw}
        return exc.code, body


def test_action_form_survives_fresh_flask_process(restart_env):
    env = restart_env
    text = (
        '<cuttle_action_form>{"mode":"choice","title":"Pick","silent":true,'
        '"options":[{"id":"a","label":"A"}]}</cuttle_action_form>'
    )
    out, n = env["rewrite_forms"](
        text, session_id=f"db_session_{env['sid']}", project_path=str(env["tmp"])
    )
    assert n == 1
    env["db"].add_message(env["sid"], "assistant", out)
    env["clear_forms"]()
    import re

    m = re.search(
        r"<cuttle_action_form_pending[^>]*>\s*(\{.*)\s*</cuttle_action_form_pending>",
        out,
        re.S,
    )
    spec = json.loads(m.group(1))
    port = _free_port()
    proc = _start_child_flask(env, port)
    try:
        _wait_http(port, proc=proc, log=env["tmp"] / "child-flask.err")
        status, data = _post_json(
            port,
            "/api/action-form/run",
            env["token"],
            {
                "token": spec.get("id"),
                "form_id": spec.get("id"),
                "spec": {**spec, "options": [{"id": "a", "label": "A", "action": "flask.restart"}]},
                "selection": {"option": "a"},
                "session_id": env["sid"],
            },
        )
        assert status == 200, data
        assert data["success"] is True
        assert "a" in (data.get("selected") or [])
        assert "flask.restart" not in (data.get("actions") or [])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_confirm_survives_fresh_interpreter(restart_env):
    env = restart_env
    actions = env["tmp"] / ".cuttle" / "actions"
    actions.mkdir(parents=True)
    (actions / "discord-post.yaml").write_text(
        "name: discord.post\ntype: discord.post\nguild_id: '1'\n"
        "channels:\n  feature-updates: '111'\n",
        encoding="utf-8",
    )
    text = (
        '<cuttle_confirm action="discord.post" channel="feature-updates">'
        "hello"
        "</cuttle_confirm>"
    )
    out, n = env["rewrite_confirms"](
        text, session_id=f"db_session_{env['sid']}", project_path=str(env["tmp"])
    )
    assert n == 1
    env["db"].add_message(env["sid"], "assistant", out)
    env["clear_pending"]()
    import re

    action_id = re.search(r'id="([a-f0-9]+)"', out).group(1)
    child = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ["CUTTLE_SRC"])
os.environ["CUTTLE_ACTION_HMAC_SECRET"] = os.environ["CUTTLE_ACTION_HMAC_SECRET"]
from api import auth_db as auth_db_mod
from api import project_actions as pa
auth_db_mod.DB_PATH = Path(os.environ["CUTTLE_TEST_DB"])
auth_db_mod._db_instance = None
db = auth_db_mod.AuthDatabase(auth_db_mod.DB_PATH)
auth_db_mod._db_instance = db
pa._hmac_secret_cache = None
from api.project_actions import handle_project_action_button
sid = os.environ["CUTTLE_SID"]
aid = os.environ["CUTTLE_AID"]
cancel = handle_project_action_button(
    f"[button:project-action-cancel] {aid}",
    session_id=f"db_session_{sid}",
)
print(json.dumps({"cancel": cancel}))
"""
    child_env = os.environ.copy()
    child_env["CUTTLE_SRC"] = str(SRC)
    child_env["CUTTLE_TEST_DB"] = str(env["db_path"])
    child_env["CUTTLE_ACTION_HMAC_SECRET"] = env["secret"]
    child_env["CUTTLE_SID"] = str(env["sid"])
    child_env["CUTTLE_AID"] = action_id
    child_env["PYTHONPATH"] = str(SRC) + os.pathsep + child_env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", child],
        cwd=str(REPO),
        env=child_env,
        capture_output=True,
        text=True, encoding="utf-8",
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["cancel"]["success"] is True
    assert "Cancelled" in str(payload["cancel"].get("response") or "")
