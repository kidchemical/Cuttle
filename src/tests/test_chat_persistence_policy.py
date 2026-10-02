"""Chat history persistence policy matrix (unified B1 expected policy).

Six real-HTTP lanes through ``POST /api/chat`` against a temporary
``AuthDatabase`` with fake executors:

- harness sync / harness SSE (``/cursor`` + ``_run_pinned_harness_turn``)
- router-family sync / SSE (``/retry`` + ``handle_router_family_command``)
- compatibility pipeline sync / stream (plain message + ``sticky_agent:
  "none"`` + faked ``maybe_route_plain_message``)

Each lane is driven across outcome shapes (success, empty failed,
failed-nonempty, ``[CANCELLED]`` text, ``ui == 'system'``, both
supervised-ownership markers, a genuinely cancelled turn, and a genuinely
superseded turn). Assertions capture the UNIFIED B1 policy: transport
completion, exactly one user row (role + content), zero unexpected
kernel/dispatch attempts, assistant row contents, and busy release (or
newer-token preservation for superseded turns). Fakes only, tmp DB only.
"""

from __future__ import annotations

import itertools
import json

import pytest

from api import chat_delivery
from api import web_chat_api as wca


# ---------------------------------------------------------------------------
# Auth DB fixture and fail-closed execution guard reused from the P5-F
# oracle suite (same tmp-DB seam) via ordinary imports: pytest applies
# the imported autouse fixture module-locally, with no session-global
# plugin registration. The positive control below proves the imported
# guard fires for THIS file.
# ---------------------------------------------------------------------------

from .test_p5f_pipeline_oracles import (  # noqa: F401
    _no_real_execution,
    authed_db,
)


def test_guard_blocks_unmocked_runners(_no_real_execution):
    """Positive control: the imported guard fires instead of spending quota."""
    from api.agent_harness import kernel as _kernel
    from api.agent_router import dispatch as _dispatch

    with pytest.raises(AssertionError, match="fail-closed test guard"):
        _kernel.run_agent_web_command("cursor", "hi", "guard-sid")
    with pytest.raises(AssertionError, match="fail-closed test guard"):
        _dispatch.execute_decision(object())
    assert _no_real_execution == {"kernel": 1, "dispatch": 1}


# ---------------------------------------------------------------------------
# Outcome payloads and behaviors.
# ---------------------------------------------------------------------------


def _result(outcome):
    base = {
        "success_text": {"success": True, "response": "matrix reply",
                         "type": "fake"},
        "empty_failed": {"success": False, "response": ""},
        "failed_nonempty": {"success": False, "response": "failed with text",
                            "type": "fake"},
        "cancelled_text": {"success": True,
                            "response": "[CANCELLED] stopped by user",
                            "type": "fake"},
        "system": {"success": True, "response": "background note",
                   "ui": "system", "type": "fake"},
        "supervised_skip": {"success": True, "response": "supervised done",
                            "skip_history_persist": True, "type": "fake"},
        "supervised_coord": {"success": True, "response": "supervised done",
                             "coordinator_response_message_id": None,
                             "type": "fake"},
    }
    return dict(base[outcome])


OUTCOMES = (
    "success_text",
    "empty_failed",
    "failed_nonempty",
    "cancelled_text",
    "system",
    "supervised_skip",
    "supervised_coord",
    "cancelled_turn",
    "superseded",
)

LANES = (
    "harness_sync",
    "harness_stream",
    "router_sync",
    "router_stream",
    "pipeline_sync",
    "pipeline_stream",
)

_counter = itertools.count()

_PIPELINE_FALLBACK = (
    "No graph is running — Cuttle chat and Discord use slash agents "
    "(`/cursor`, `/codex`, …) or the agent router. Pick an agent chip, "
    "or send a plain message for the router."
)


def _numeric_sid(value):
    if isinstance(value, int):
        return value
    text = str(value)
    if text.startswith("db_session_"):
        return int(text[len("db_session_"):])
    return int(text)


def _install_lane_fakes(monkeypatch, lane, outcome, db):
    """Fake the lane's executor seam; behavior outcomes act on delivery."""
    if lane.startswith("harness"):
        def fake_run(agent_id, prompt, chat_session_id, **kwargs):
            return _runner_result(db, outcome, chat_session_id)

        monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    elif lane.startswith("router"):
        import api.agent_router.integration as integration

        def fake_router(message_content, session_id=None, project_path=None,
                        status_queue=None):
            return _runner_result(db, outcome, session_id)

        monkeypatch.setattr(
            integration, "handle_router_family_command", fake_router
        )
    else:
        import api.agent_router.integration as integration

        def fake_plain(message_content, session_id=None, **kwargs):
            if outcome == "success_text":
                return None  # abstain -> owned no-LLM fallback
            return _runner_result(db, outcome, session_id)

        monkeypatch.setattr(
            integration, "maybe_route_plain_message", fake_plain
        )


def _runner_result(db, outcome, session_key):
    sid = _numeric_sid(session_key)
    if outcome == "cancelled_turn":
        chat_delivery.cancel_current_turn(sid)
        return {"success": True, "response": "late reply", "type": "fake"}
    if outcome == "superseded":
        # Hand the slot to a newer turn and hold it: the in-flight result
        # is stale the moment it returns.
        chat_delivery.end(sid)
        assert chat_delivery.try_begin(sid) is True
        return {"success": True, "response": "stale reply", "type": "fake"}
    if outcome in ("supervised_skip", "supervised_coord"):
        # Supervised orchestration owns its canonical assistant row and
        # updates it in place; the marker tells the saver to stay out. The
        # coordinator marker carries the REAL canonical row id returned by
        # add_message, so exactly-once counts have meaning.
        canonical_id = db.add_message(
            sid, "assistant", "canonical supervised row",
            metadata={"supervised": True},
        )
        marker = _result(outcome)
        if outcome == "supervised_coord":
            marker["coordinator_response_message_id"] = canonical_id
        return marker
    return _result(outcome)


def _lane_message(lane, tag):
    if lane.startswith("harness"):
        return f"/cursor matrix probe {tag}"
    if lane.startswith("router"):
        return f"/retry matrix probe {tag}"
    return f"plain matrix probe {tag}"


def _post_lane(client, lane, sid, tag):
    payload = {"message": _lane_message(lane, tag), "session_id": sid}
    if lane in ("harness_sync", "router_sync", "pipeline_sync"):
        payload["stream"] = False
    if lane.startswith("pipeline"):
        payload["sticky_agent"] = "none"
    return client.post("/api/chat", json=payload)


def _event_types(text):
    out = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            out.append(json.loads(line[len("data: "):]).get("type"))
        except ValueError:
            out.append("<bad-json>")
    return out


def _assistant_rows(db, sid):
    return [m for m in db.get_messages(sid) if m["role"] == "assistant"]


def _release_check(sid):
    assert chat_delivery.try_begin(sid) is True  # idle + released
    chat_delivery.end(sid)


def _newer_held_check(sid):
    assert chat_delivery.try_begin(sid) is False  # newer turn keeps slot
    chat_delivery.end(sid)  # release the superseding turn
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


# ---------------------------------------------------------------------------
# Matrix: 6 lanes x 9 outcomes. Expected values are the unified B1
# policy (same outcomes in every lane except the pipeline fallback
# success text).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("outcome", OUTCOMES)
def test_history_policy_matrix(authed_db, monkeypatch, lane, outcome,
                               _no_real_execution):
    client, db, _uid = authed_db
    _install_lane_fakes(monkeypatch, lane, outcome, db)
    sid = db.create_chat_session(_uid)
    tag = f"{lane}-{outcome}-{next(_counter)}"

    res = _post_lane(client, lane, sid, tag)
    assert res.status_code == 200
    is_sync = lane.endswith("_sync")
    if is_sync:
        body = res.get_json()
        assert "response" in body and "success" in body
    else:
        kinds = _event_types(res.get_data(as_text=True))
        assert kinds[0] == "session"
        assert kinds[-1] == "done"

    rows = db.get_messages(sid)
    expected = _expected_assistant_contents(lane, outcome)
    assert [m["role"] for m in rows] == ["user"] + ["assistant"] * len(
        expected
    )
    assert rows[0]["content"] == _lane_message(lane, tag)
    assert _no_real_execution == {"kernel": 0, "dispatch": 0}
    assistants = _assistant_rows(db, sid)

    assert [m["content"] for m in assistants] == expected

    if outcome == "superseded":
        _newer_held_check(sid)
    else:
        _release_check(sid)


def _expected_assistant_contents(lane, outcome):
    """Unified B1 policy per lane x outcome, same expected outcomes in all
    lanes except the pipeline fallback success text.

    The shared saver (`make_assistant_saver`) is the sole eligibility
    policy: cancelled-text/system/supervised-owned rows never persist;
    useful failure text is retained everywhere, including sync lanes
    (coarse kept-rule gates); superseded claimed turns save nothing
    while the newer turn keeps the busy slot.
    """
    if outcome == "success_text":
        if lane.startswith("pipeline"):
            return [_PIPELINE_FALLBACK]
        return ["matrix reply"]
    if outcome in ("empty_failed", "cancelled_turn"):
        return []
    if outcome == "failed_nonempty":
        return ["failed with text"]
    if outcome in ("cancelled_text", "system"):
        return []
    if outcome in ("supervised_skip", "supervised_coord"):
        return ["canonical supervised row"]
    if outcome == "superseded":
        return []
    raise AssertionError(f"unknown outcome {outcome}")
