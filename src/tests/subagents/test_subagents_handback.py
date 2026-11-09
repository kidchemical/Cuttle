"""A ``--wait`` round that outlives the agent which launched it (``api.subagents.handback``).

CH-002006: a parent's Claude turn stopped waiting on ``subagents spawn --wait``;
the batch finished later and nobody told the parent. The CLI now hands the
round back to the parent chat (a Cuttle ``notice`` turn) when its launching
process is gone, and stays quiet while that process still listens.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.chat_ops import store  # noqa: E402
from api.subagents import handback  # noqa: E402


def _gone_listener():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return {"pid": p.pid, "create_time": time.time() - 5, "name": "claude"}


def _live_listener():
    import psutil

    return {"pid": os.getpid(), "create_time": psutil.Process().create_time(), "name": "python"}


def _batch(parent=8201):
    return {
        "id": "b-123", "parent_session_id": parent, "status": "completed",
        "children": [
            {"handle": "CH-008202", "label": "Builder", "agent": "claude", "model": "claude-opus-5-5",
             "status": "completed", "result": "Built flashlight-grip-v1.blend\nwith 13 renders.", "error": ""},
            {"handle": "CH-008203", "label": "Reviewer", "agent": "codex", "model": "",
             "status": "failed", "result": "", "error": "quota exhausted"},
        ],
    }


def _notices(sid):
    return [o for o in store.listing(sid) if o["kind"] == "notice"]


def test_listener_checks():
    assert handback.listener_gone(None) is False
    assert handback.listener_gone(_live_listener()) is False
    assert handback.listener_gone(_gone_listener()) is True
    found = handback.capture_listener()
    assert found is None or (found["pid"] > 1 and found["name"] not in handback._WRAPPERS)


def test_gone_listener_queues_the_batch_on_the_parent():
    op = handback.hand_back(_gone_listener(), _batch(8201), verb="spawn")
    assert op and op["target"] == op["actor"] == 8201
    (queued,) = _notices(8201)
    assert queued["payload"]["label"] == "Sub-agents finished"
    text = queued["payload"]["text"]
    assert "Sub-agent batch b-123" in text and "finished after your turn stopped listening" in text
    assert "- CH-008202 Builder (claude claude-opus-5-5): completed" in text
    assert "Built flashlight-grip-v1.blend with 13 renders." in text
    assert "- CH-008203 Reviewer (codex): failed" in text and "quota exhausted" in text
    assert "status --batch b-123" in text


def test_live_listener_gets_the_result_the_normal_way():
    assert handback.hand_back(_live_listener(), _batch(8204)) is None
    assert _notices(8204) == []


def test_message_focuses_on_the_messaged_child_and_timeout_says_cancelled():
    text = handback.notice_text(_batch(), verb="message", focus="CH-008203")
    assert "CH-008203" in text and "CH-008202" not in text
    assert "cancelled" in handback.notice_text(_batch(), timed_out=True)
    long = {**_batch(), "children": [{**_batch()["children"][0], "result": "x" * 9000}]}
    assert len(handback.notice_text(long)) < 3500


def test_spawn_cli_hands_back_when_its_launcher_is_gone(monkeypatch, capsys):
    from api.subagents import cli, service

    monkeypatch.setattr(handback, "capture_listener", _gone_listener)
    monkeypatch.setattr(service, "spawn", lambda **kw: {**_batch(kw["parent_session_id"]), "ok": True})
    child = json.dumps({"title": "Builder", "message": "go"})
    assert cli.main(["spawn", "--parent", "CH-008205", "--wait", "--json", "--child", child]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "b-123"
    assert len(_notices(8205)) == 1

    monkeypatch.setattr(handback, "capture_listener", _live_listener)
    assert cli.main(["spawn", "--parent", "CH-008206", "--wait", "--json", "--child", child]) == 0
    assert _notices(8206) == []
