"""Pipeline-lane history tests for the B1 unified saver policy.

Both pipeline lanes persist through the shared ``make_assistant_saver``
(no lane-local guards, no persist-anything callback). These tests pin,
over real HTTP with fake executors and the REAL owned rewriter:

- a rewritten action card is consistent between the persisted row and
  the streamed/sync wire response (exactly-once rewrite);
- assistant rows carry the captured request/project metadata;
- the titler hook runs once per kept turn.
"""

from __future__ import annotations

import json

import pytest

from .test_p5f_pipeline_oracles import (  # noqa: E402,F401
    _announced_sid,
    _event_types,
    _no_real_execution,
    authed_db,
)


CARD = (
    "<cuttle_action_form>\n"
    '{"title":"Restart Flask (daemon-owned)","mode":"choice","options":['
    '{"id":"graceful","label":"Graceful","action":"flask.restart",'
    '"params":{"mode":"graceful"}}]}\n'
    "</cuttle_action_form>\n"
)


@pytest.fixture
def isolated_forms(monkeypatch, tmp_path):
    """Isolated HMAC secret + deterministic restart generation."""
    import api.project_actions as pa
    from api.action_forms import clear_forms_for_tests

    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "b1-pipeline-history")
    monkeypatch.setattr(pa, "_hmac_secret_cache", None)
    clear_forms_for_tests()
    monkeypatch.setenv("CUTTLE_FLASK_GENERATION", "7")
    return tmp_path


@pytest.fixture
def card_executor(monkeypatch):
    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration,
        "maybe_route_plain_message",
        lambda *a, **k: {"success": True, "response": CARD, "type": "plain"},
    )


@pytest.fixture
def titler_calls(monkeypatch):
    import api.chat_titler as titler

    calls = []
    monkeypatch.setattr(
        titler, "schedule_session_autoname", lambda *a, **k: calls.append(a)
    )
    return calls


def _post_pipeline(client, sid, message, stream):
    payload = {
        "message": message,
        "sticky_agent": "none",
        "session_id": sid,
    }
    if not stream:
        payload["stream"] = False
    return client.post("/api/chat", json=payload)


def _assistant_rows(db, sid):
    return [m for m in db.get_messages(sid) if m["role"] == "assistant"]


def test_pipeline_sync_card_row_matches_wire_body(
    authed_db, isolated_forms, card_executor, titler_calls
):
    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    res = _post_pipeline(client, sid, "plain card sync", stream=False)
    assert res.status_code == 200
    body = res.get_json()
    rows = _assistant_rows(db, sid)
    assert len(rows) == 1
    row = rows[0]["content"]
    assert "<cuttle_action_form>" not in row  # rewritten, not raw
    assert 'id="flask-restart-g7"' in row
    assert row.count("flask-restart-g7") == body["response"].count(
        "flask-restart-g7"
    )
    assert body["response"] == row
    assert len(titler_calls) == 1


def test_pipeline_stream_card_row_matches_done_event(
    authed_db, isolated_forms, card_executor, titler_calls
):
    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    res = _post_pipeline(client, sid, "plain card stream", stream=True)
    assert res.status_code == 200
    text = res.get_data(as_text=True)
    kinds = _event_types(text)
    assert kinds[0] == "session"
    assert kinds[-1] == "done"
    done = None
    for line in text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[len("data: "):])
            if payload.get("type") == "response":
                done = payload
    assert done is not None
    rows = _assistant_rows(db, sid)
    assert len(rows) == 1
    row = rows[0]["content"]
    assert "<cuttle_action_form>" not in row
    assert 'id="flask-restart-g7"' in row
    # Exactly-once rewrite: the done payload carries the same single
    # rewritten card the saver persisted (adapter re-application is a
    # no-op on already-rewritten markup).
    assert done["response"] == row
    assert len(titler_calls) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_pipeline_rows_carry_captured_project_metadata(
    authed_db, isolated_forms, card_executor, tmp_path, stream, monkeypatch
):
    import threading

    import api.web_chat_api as _wca

    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    proj, pname, pid = str(proj_dir), "B1Probe", 987654

    class _FakeCatalog:
        """Isolated catalog: one unique registered project. No
        delegation — unexpected manager use fails loudly."""

        def get_projects(self):
            return [{"id": pid, "name": pname, "path": proj}]

        def get_project(self, project_id):
            if int(project_id) == pid:
                return {"id": pid, "name": pname, "path": proj}
            return None

    monkeypatch.setattr(_wca, "project_manager", _FakeCatalog())

    main_ident = threading.get_ident()
    saves = []
    real_add = db.add_message

    def _recording_add(session_id, role, content, metadata=None):
        if role == "assistant":
            saves.append(threading.get_ident())
        return real_add(session_id, role, content, metadata=metadata)

    monkeypatch.setattr(db, "add_message", _recording_add)

    factory_idents = []
    real_factory = _wca._make_auth_assistant_saver

    def _recording_factory(*a, **k):
        factory_idents.append(threading.get_ident())
        return real_factory(*a, **k)

    monkeypatch.setattr(_wca, "_make_auth_assistant_saver", _recording_factory)

    payload = {
        "message": "plain project probe",
        "sticky_agent": "none",
        "session_id": sid,
        "project_id": pid,
        "project_path": proj,
        "project_name": pname,
    }
    if not stream:
        payload["stream"] = False
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    if stream:
        # Consume the streamed response before reading rows: the worker
        # only runs once the client reads.
        res.get_data(as_text=True)
    rows = db.get_messages(sid)
    assistants = [m for m in rows if m["role"] == "assistant"]
    assert assistants, "kept turn must persist"
    row = assistants[-1]["content"]
    assert 'id="flask-restart-g7"' in row
    assistant_meta = assistants[-1].get("metadata") or {}
    # Exact supplied snapshot from the isolated fake catalog (project_id
    # is authoritative); the legacy pipeline callback persisted no
    # project metadata at all. The user row in this lane never carried a
    # stamp — pre-existing route behavior, unchanged by B1.
    assert assistant_meta.get("project_id") == pid
    assert assistant_meta.get("project_path") == proj
    assert assistant_meta.get("project_name") == pname
    assert factory_idents and all(i == main_ident for i in factory_idents)
    assert saves, "assistant save must flow through the saver"
    if stream:
        # The worker thread has no Flask request context: exact metadata
        # here proves the snapshot was captured in the request thread.
        assert saves[-1] != main_ident


@pytest.mark.parametrize("stream", [False, True])
def test_pipeline_project_switch_midturn_keeps_request_context(
    authed_db, isolated_forms, titler_calls, tmp_path, stream, monkeypatch
):
    """Mid-turn session switch: metadata pins the request, card follows
    the session (existing owner precedence, characterized not changed).

    The fake executor switches the stored session project to B before
    returning; the request identified A. Persisted metadata stays on A
    (B1 fallback preservation); the rewritten pending-card JSON follows
    the live session chip to B, exactly as the shared rewriter
    (`action_forms.rewrite_action_forms`: session path wins) has always
    done — including under the legacy pipeline callback.
    """
    import re

    import api.web_chat_api as _wca

    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    a_dir, b_dir = tmp_path / "projA", tmp_path / "projB"
    a_dir.mkdir()
    b_dir.mkdir()
    A = {"id": 987654, "name": "B1ProbeA", "path": str(a_dir)}
    B = {"id": 987655, "name": "B1ProbeB", "path": str(b_dir)}

    class _FakeCatalog:
        def get_projects(self):
            return [dict(A), dict(B)]

        def get_project(self, project_id):
            for rec in (A, B):
                if int(project_id) == rec["id"]:
                    return dict(rec)
            return None

    monkeypatch.setattr(_wca, "project_manager", _FakeCatalog())

    def _switch_then_card(*a, **k):
        db.set_session_project(
            sid, uid, project_id=B["id"], project_name=B["name"],
            project_path=B["path"],
        )
        return {"success": True, "response": CARD, "type": "plain"}

    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration, "maybe_route_plain_message", _switch_then_card
    )

    payload = {
        "message": "plain project switch probe",
        "sticky_agent": "none",
        "session_id": sid,
        "project_id": A["id"],
        "project_path": A["path"],
        "project_name": A["name"],
    }
    if not stream:
        payload["stream"] = False
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    if stream:
        # Drain before asserting: the worker only runs on client read.
        text = res.get_data(as_text=True)
        kinds = _event_types(text)
        assert kinds[-1] == "done"
        wire = None
        for line in text.splitlines():
            if line.startswith("data: "):
                event = json.loads(line[len("data: "):])
                if event.get("type") == "response":
                    wire = event["response"]
        assert wire is not None
    else:
        wire = res.get_json()["response"]
    rows = _assistant_rows(db, sid)
    assert len(rows) == 1
    row = rows[0]["content"]
    assert 'id="flask-restart-g7"' in row
    assert wire == row
    meta = rows[0].get("metadata") or {}
    assert meta.get("project_id") == A["id"]
    assert meta.get("project_path") == A["path"]
    assert meta.get("project_name") == A["name"]
    match = re.search(
        r"<cuttle_action_form_pending\b[^>]*>([\s\S]*?)"
        r"</cuttle_action_form_pending>",
        row,
    )
    assert match is not None
    # Owner precedence, unchanged by B1: the live session chip wins the
    # card embed. Only the persisted metadata is pinned to the request.
    assert json.loads(match.group(1)).get("project_path") == B["path"]
    assert len(titler_calls) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_pipeline_session_project_cleared_midturn_falls_back_to_request(
    authed_db, isolated_forms, titler_calls, tmp_path, stream, monkeypatch
):
    """A cleared session project must fall back to the request project.

    This is the actual legacy fallback the B1 saver replacement must
    preserve: the fake executor erases the stored session project before
    returning, so the shared saver has no session chip to defer to and
    must use the ingress-resolved (fallback) project — in both the
    persisted metadata and the rewritten pending-card JSON.
    """
    import re

    import api.web_chat_api as _wca

    client, db, uid = authed_db
    sid = db.create_chat_session(uid)
    a_dir = tmp_path / "projA"
    a_dir.mkdir()
    A = {"id": 987654, "name": "B1ProbeA", "path": str(a_dir)}

    class _FakeCatalog:
        def get_projects(self):
            return [dict(A)]

        def get_project(self, project_id):
            if int(project_id) == A["id"]:
                return dict(A)
            return None

    monkeypatch.setattr(_wca, "project_manager", _FakeCatalog())

    def _clear_then_card(*a, **k):
        db.set_session_project(sid, uid)
        return {"success": True, "response": CARD, "type": "plain"}

    import api.agent_router.integration as integration

    monkeypatch.setattr(
        integration, "maybe_route_plain_message", _clear_then_card
    )

    payload = {
        "message": "plain project fallback probe",
        "sticky_agent": "none",
        "session_id": sid,
        "project_id": A["id"],
        "project_path": A["path"],
        "project_name": A["name"],
    }
    if not stream:
        payload["stream"] = False
    res = client.post("/api/chat", json=payload)
    assert res.status_code == 200
    if stream:
        # Drain before asserting: the worker only runs on client read.
        text = res.get_data(as_text=True)
        kinds = _event_types(text)
        assert kinds[-1] == "done"
        wire = None
        for line in text.splitlines():
            if line.startswith("data: "):
                event = json.loads(line[len("data: "):])
                if event.get("type") == "response":
                    wire = event["response"]
        assert wire is not None
    else:
        wire = res.get_json()["response"]
    rows = _assistant_rows(db, sid)
    assert len(rows) == 1
    row = rows[0]["content"]
    assert 'id="flask-restart-g7"' in row
    assert wire == row
    meta = rows[0].get("metadata") or {}
    assert meta.get("project_id") == A["id"]
    assert meta.get("project_path") == A["path"]
    assert meta.get("project_name") == A["name"]
    match = re.search(
        r"<cuttle_action_form_pending\b[^>]*>([\s\S]*?)"
        r"</cuttle_action_form_pending>",
        row,
    )
    assert match is not None
    # No session chip survives the clear, so the shared saver's fallback
    # (ingress-resolved project A) reaches the card embed too.
    assert json.loads(match.group(1)).get("project_path") == A["path"]
    assert len(titler_calls) == 1
