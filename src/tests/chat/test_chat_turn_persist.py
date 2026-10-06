"""Phase 5 P5-C: persistence/request-data boundary pins.

The assistant saver, user-turn persist, and stream delivery must behave
identically once constructed by `api.chat_turn_persist` with explicit
``db``/``request_data`` instead of reading the Flask request/singleton
inside the coordinator. Owner unit tests target the new module; the SSE
test drives the real route before and after the move.
"""

from __future__ import annotations

import pytest


USER = {"id": 7, "username": "owner", "email": "owner@example.com"}


class FakeDB:
    def __init__(self):
        self.rows = []

    def verify_auth_session(self, token):
        return dict(USER) if token == "tok-seam" else None

    def add_message(self, chat_session_id, role, content, metadata=None):
        self.rows.append({
            "session": chat_session_id, "role": role,
            "content": content, "metadata": metadata,
        })
        return len(self.rows)

    def get_chat_session(self, *a, **k):
        return {"id": 777}

    def set_session_project(self, *a, **k):
        return True

    def update_session_activity(self, *a, **k):
        return None


def _badge_fn(message_text, session_id, identity=None):
    if identity and identity.get("agent") == "cursor":
        return {"slash_command": {"chips": [{"label": "Cursor"}]}}
    return None


def _merge_fn(meta, request_data, session_id, cursor_run=None):
    out = dict(meta or {})
    proj = (request_data or {}).get("project_name")
    if proj and "project_name" not in out:
        out["project_name"] = proj
    return out


def _saver_deps(db, request_data=None, assistant_meta=None):
    from api import chat_turn_persist as persist

    return dict(
        db=db,
        request_data=request_data or {"project_name": "Cuttle"},
        resolve_project=lambda data: "",
        assistant_meta_fn=assistant_meta or (lambda res: {"usage": {"t": 1}}),
        project_merge_fn=_merge_fn,
        schedule_autoname=lambda *a, **k: None,
        project_root="/repo",
    )


# --------------------------------------------------------------------------
# persist_user_turn
# --------------------------------------------------------------------------


def test_persist_user_turn_attaches_badge_and_project():
    from api import chat_turn_persist as persist

    db = FakeDB()
    persist.persist_user_turn(
        db, 11,
        message_text="/cursor hi",
        history_text="/cursor hi",
        base_meta={},
        identity={"agent": "cursor"},
        badge_fn=_badge_fn,
        project_merge_fn=_merge_fn,
        request_data={"project_name": "Cuttle"},
    )
    assert len(db.rows) == 1
    row = db.rows[0]
    assert (row["session"], row["role"], row["content"]) == (11, "user", "/cursor hi")
    assert row["metadata"]["slash_command"] == {"chips": [{"label": "Cursor"}]}
    assert row["metadata"]["project_name"] == "Cuttle"


def test_persist_user_turn_keeps_existing_chips_and_note_text():
    from api import chat_turn_persist as persist

    db = FakeDB()
    persist.persist_user_turn(
        db, 12,
        message_text="/cursor hi",
        history_text="[Attached: shot.png]",
        base_meta={"slash_command": {"chips": [{"label": "Kept"}]},
                   "attachments": [{"filename": "shot.png"}]},
        identity={"agent": "cursor"},
        badge_fn=_badge_fn,
        project_merge_fn=_merge_fn,
        request_data={},
    )
    meta = db.rows[0]["metadata"]
    assert meta["slash_command"] == {"chips": [{"label": "Kept"}]}
    assert db.rows[0]["content"] == "[Attached: shot.png]"


def test_persist_user_turn_noops_on_empty_or_missing_session():
    from api import chat_turn_persist as persist

    db = FakeDB()
    persist.persist_user_turn(
        db, None, message_text="hi", history_text="hi", base_meta={},
        identity=None, badge_fn=_badge_fn, project_merge_fn=_merge_fn,
        request_data={},
    )
    persist.persist_user_turn(
        db, 13, message_text="", history_text="", base_meta={},
        identity=None, badge_fn=_badge_fn, project_merge_fn=_merge_fn,
        request_data={},
    )
    assert db.rows == []


def test_persist_user_turn_contains_merge_errors():
    from api import chat_turn_persist as persist

    def bad_merge(*a, **k):
        raise RuntimeError("merge down")

    db = FakeDB()
    persist.persist_user_turn(
        db, 14, message_text="hi", history_text="hi", base_meta={},
        identity=None, badge_fn=_badge_fn, project_merge_fn=bad_merge,
        request_data={},
    )
    assert db.rows == []


# --------------------------------------------------------------------------
# make_assistant_saver
# --------------------------------------------------------------------------


def test_saver_factory_returns_none_without_session():
    from api import chat_turn_persist as persist

    assert persist.make_assistant_saver(chat_session_id=None, **_saver_deps(FakeDB())) is None


def test_saver_persists_success_with_meta_and_project():
    from api import chat_turn_persist as persist

    db = FakeDB()
    saver = persist.make_assistant_saver(chat_session_id=21, **_saver_deps(db))
    assert saver is not None
    saver({"success": True, "response": "done it", "type": "cursor"})
    assert len(db.rows) == 1
    row = db.rows[0]
    assert (row["session"], row["role"], row["content"]) == (21, "assistant", "done it")
    assert row["metadata"]["usage"] == {"t": 1}
    assert row["metadata"]["project_name"] == "Cuttle"


@pytest.mark.parametrize("result", [
    {"success": True, "skip_history_persist": True, "response": "x"},
    {"success": True, "coordinator_response_message_id": 5, "response": "x"},
    {"success": False, "response": ""},
    {"success": False, "response": "[CANCELLED] stopped"},
    {"success": True, "response": "x", "ui": "system"},
])
def test_saver_skips_non_history_results(result):
    from api import chat_turn_persist as persist

    db = FakeDB()
    saver = persist.make_assistant_saver(chat_session_id=22, **_saver_deps(db))
    saver(dict(result))
    assert db.rows == []


def test_saver_skips_when_turn_cancelled(monkeypatch):
    from api import chat_turn_persist as persist
    from api import chat_delivery

    db = FakeDB()
    saver = persist.make_assistant_saver(chat_session_id=23, **_saver_deps(db))
    monkeypatch.setattr(chat_delivery, "is_turn_cancelled", lambda sid: True)
    saver({"success": True, "response": "late"})
    assert db.rows == []


# --------------------------------------------------------------------------
# persist_auth_user_message
# --------------------------------------------------------------------------


def test_persist_auth_user_message_merges_project_and_writes():
    from api import chat_turn_persist as persist

    db = FakeDB()
    persist.persist_auth_user_message(
        db, 31, "hello",
        metadata=None, project_merge_fn=_merge_fn,
        request_data={"project_name": "Cuttle"},
    )
    assert db.rows[0]["metadata"] == {"project_name": "Cuttle"}


def test_persist_auth_user_message_noops_on_empty():
    from api import chat_turn_persist as persist

    db = FakeDB()
    persist.persist_auth_user_message(db, None, "hello", metadata=None,
                                      project_merge_fn=_merge_fn, request_data={})
    persist.persist_auth_user_message(db, 31, "", metadata=None,
                                      project_merge_fn=_merge_fn, request_data={})
    assert db.rows == []


# --------------------------------------------------------------------------
# Route SSE integration (real route, fake runner + fake DB)
# --------------------------------------------------------------------------


@pytest.fixture
def sse_env(monkeypatch):
    from api import web_chat_api as wca

    db = FakeDB()
    user = {"id": 7, "username": "owner", "email": "owner@example.com"}
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(wca, "get_auth_db", lambda: db)
    monkeypatch.setattr(
        wca, "_resolve_auth_chat_session", lambda sid: (dict(user), 777, False)
    )
    monkeypatch.setattr(wca, "_stamp_auth_session_project", lambda *a, **k: None)
    monkeypatch.setattr(
        "api.starred_slash.apply_default_sticky_prefix", lambda m, *a, **k: m
    )
    monkeypatch.setattr(
        "api.chat_titler.schedule_session_autoname", lambda *a, **k: None
    )

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        return {"success": True, "response": "did sse", "type": "cursor"}

    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    return wca, db


def test_stream_harness_lane_delivers_sse_and_persists_both_rows(sse_env):
    from api import chat_delivery

    wca, db = sse_env
    client = wca.app.test_client()
    client.set_cookie("session_token", "tok-seam")
    res = client.post("/api/chat", json={
        "message": "/cursor sse please", "session_id": "seam-sse1", "stream": True,
    })
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    assert "db_session_" in text or "777" in text
    assert "did sse" in text
    assert text.rstrip().endswith("data: {\"type\": \"done\"}")
    roles = [(r["session"], r["role"]) for r in db.rows]
    assert roles == [(777, "user"), (777, "assistant")]
    assert chat_delivery.is_busy(777) is False



def test_routing_badge_survives_saver_metadata_merge(monkeypatch):
    from api.chat_turn_persist import make_assistant_saver
    import api.chat_delivery
    monkeypatch.setattr(api.chat_delivery, 'is_turn_cancelled', lambda _: False)
    db = FakeDB()
    badge = {'agent': 'codex', 'kind': 'routed', 'reason': 'Small change'}
    saver = make_assistant_saver(chat_session_id=777, **_saver_deps(db))
    saver({'success': True, 'response': 'Done', 'routing_badge': badge})
    assert db.rows[-1]['metadata']['routing_badge'] == badge
    assert db.rows[-1]['metadata']['project_name'] == 'Cuttle'


@pytest.mark.parametrize('stream', [False, True])
def test_routing_badge_real_http_transport_and_persistence(sse_env, monkeypatch, stream):
    import json
    wca, db = sse_env
    badge = {'agent': 'codex', 'kind': 'routed', 'reason': 'Small change'}
    monkeypatch.setattr(wca, '_run_pinned_harness_turn', lambda *a, **k: {
        'success': True, 'response': 'Done', 'type': 'codex', 'routing_badge': badge})
    client = wca.app.test_client()
    client.set_cookie('session_token', 'tok-seam')
    response = client.post('/api/chat', json={'message': '/codex test', 'session_id': 'seam-badge', 'stream': stream})
    assert response.status_code == 200
    if stream:
        events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith('data: ')]
        wire = next(e for e in events if e['type'] == 'response')
    else:
        wire = response.get_json()
    assert wire['routing_badge'] == badge
    assert db.rows[-1]['metadata']['routing_badge'] == badge
