"""My Cuttle Performance: pinned-turn outcomes, history backfill, feedback, grouping."""

from __future__ import annotations

import json
import sqlite3

from api.agent_router import outcomes
from api.agent_router.pinned_outcomes import (
    backfill_from_history,
    classify_turn,
    provider_for,
    record_pinned_turn,
    split_model_effort,
)
from api.dashboards import service


def test_split_model_effort():
    assert split_model_effort("cursor-grok-4.6-high-fast") == ("grok-4.6-fast", "high")
    assert split_model_effort("claude-4.6-sonnet-medium-thinking") == ("claude-4.6-sonnet-thinking", "medium")
    assert split_model_effort("Auto") == ("auto", None)
    assert split_model_effort("") == ("default", None)
    assert split_model_effort("muse-spark-1.3-contributor") == ("muse-spark-1.3-contributor", None)


def test_provider_for():
    assert provider_for("cursor", "grok-4.6") == "xAI"
    assert provider_for("cursor", "auto") == "Cursor"
    assert provider_for("codex", "gpt-6-luna") == "OpenAI"
    assert provider_for("muse", "default") == "Meta"


def test_classify_turn_ignores_transport_words_in_good_answers():
    ok = {"success": True, "type": "cursor_command", "response": "The file was not found, rate limit fine."}
    assert classify_turn(ok) == ("none", "")
    assert classify_turn({"type": "cursor_error", "response": "[FAIL] exited with code 1"})[0] == "task"
    assert classify_turn({"type": "codex_error", "response": "429 Too Many Requests"})[0] == "transport"
    assert classify_turn({"type": "muse_error", "response": "[CANCELLED] Muse run was cancelled"})[0] == "cancelled"


def test_record_pinned_turn_and_skip_meta(tmp_path):
    db = tmp_path / "o.db"
    body = {
        "success": True, "type": "codex_command", "response": "done",
        "agent_id": "codex", "agent_model": "gpt-6-luna", "agent_effort": "high",
        "query_id": "q1", "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.02},
    }
    assert record_pinned_turn("codex", body, latency_ms=4200, session_id=7, db_path=db)
    assert not record_pinned_turn("codex", {**body, "query_id": "q2", "meta_command": True}, latency_ms=5, db_path=db)
    rows = outcomes.all_outcomes(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert (row["source"], row["target_agent"], row["target_model"], row["reasoning_effort"]) == (
        "pinned", "codex", "gpt-6-luna", "high"
    )
    assert row["cost"] == 0.02 and row["completion_tokens"] == 5 and row["success"] == 1


def test_cursor_pin_uses_requested_model(tmp_path):
    db = tmp_path / "o.db"
    body = {
        "success": True, "type": "cursor_command", "response": "ok", "agent_id": "cursor",
        "agent_model": "Grok 4.6", "query_id": "c1",
        "cursor_run": {"requested_model": "cursor-grok-4.6-medium", "reported_model": "Grok 4.6"},
    }
    record_pinned_turn("cursor", body, latency_ms=1000, db_path=db)
    row = outcomes.all_outcomes(db_path=db)[0]
    assert row["target_model"] == "grok-4.6"
    assert row["reasoning_effort"] == "medium"


def test_schema_migration_adds_effort_column(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE router_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, recorded_at REAL NOT NULL, "
        "decision_id TEXT NOT NULL, attempt_index INTEGER NOT NULL, session_id TEXT, project_path TEXT, "
        "task_type TEXT NOT NULL, difficulty TEXT NOT NULL, strategy TEXT NOT NULL, target_agent TEXT NOT NULL, "
        "target_model TEXT NOT NULL, source TEXT NOT NULL, success INTEGER NOT NULL, failure_kind TEXT NOT NULL, "
        "reason TEXT, latency_ms REAL, query_id TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, "
        "total_tokens INTEGER, cost REAL, user_feedback TEXT, UNIQUE(decision_id, attempt_index))"
    )
    conn.commit()
    conn.close()
    assert outcomes.all_outcomes(db_path=db) == []
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(router_outcomes)")}
    assert "reasoning_effort" in cols


def _auth_db(path, messages):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, chat_session_id INTEGER, role TEXT, "
        "content TEXT, timestamp TEXT, metadata TEXT)"
    )
    for m in messages:
        conn.execute(
            "INSERT INTO chat_messages (id, chat_session_id, role, content, timestamp, metadata) VALUES (?,?,?,?,?,?)",
            (m["id"], m.get("sid", 1), m["role"], m.get("content", ""), m["ts"],
             json.dumps(m["meta"]) if m.get("meta") is not None else None),
        )
    conn.commit()
    conn.close()


def test_backfill_from_history_is_idempotent(tmp_path):
    auth = tmp_path / "auth.db"
    db = tmp_path / "o.db"
    _auth_db(auth, [
        {"id": 1, "role": "user", "ts": "2026-09-01 10:00:00"},
        {"id": 2, "role": "assistant", "ts": "2026-09-01 10:01:30", "content": "Fixed it.",
         "meta": {"query_id": "h1", "user_feedback": "good",
                  "slash_command": {"chips": [{"label": "Codex - GPT-6-Luna · high",
                                               "meta": "/codex · model gpt-6-luna · effort high",
                                               "category": "command"}]},
                  "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140, "cost": 0.05}}},
        {"id": 3, "role": "user", "ts": "2026-09-01 11:00:00"},
        {"id": 4, "role": "assistant", "ts": "2026-09-01 11:00:20",
         "content": "[FAIL] **Cursor Agent** (`agent`):\nError: exited with code 1",
         "meta": {"query_id": "h2", "slash_command_failed": True,
                  "slash_command": {"chips": [{"label": "Cursor Agent", "meta": "/cursor", "category": "cursor"}]},
                  "cursor_run": {"requested_model": "auto", "usage": {"inputTokens": 5, "outputTokens": 7}}}},
        {"id": 5, "role": "assistant", "ts": "2026-09-01 11:00:25", "content": "demo",
         "meta": {"slash_command": {"chips": [{"label": "Codex", "meta": "/codex", "category": "codex"}]}}},
        {"id": 6, "role": "user", "ts": "2026-09-01 12:00:00"},
        {"id": 7, "role": "assistant", "ts": "2026-09-01 12:00:01",
         "content": "**Muse Code:** Model set to `muse-spark-1.3` for this chat.",
         "meta": {"query_id": "h3", "slash_command": {"chips": [{"label": "Muse", "meta": "/muse", "category": "muse"}]}}},
    ])
    first = backfill_from_history(auth_db_path=auth, db_path=db)
    assert first["inserted"] == 2
    second = backfill_from_history(auth_db_path=auth, db_path=db)
    assert second["inserted"] == 0 and second["already_recorded"] == 2

    rows = {r["query_id"]: r for r in outcomes.all_outcomes(db_path=db)}
    codex = rows["h1"]
    assert (codex["target_agent"], codex["target_model"], codex["reasoning_effort"]) == ("codex", "gpt-6-luna", "high")
    assert codex["latency_ms"] == 90_000
    assert codex["user_feedback"] == "good"
    assert codex["cost"] == 0.05
    cursor = rows["h2"]
    assert cursor["failure_kind"] == "task" and cursor["completion_tokens"] == 7


def test_set_feedback_for_query(tmp_path):
    db = tmp_path / "o.db"
    record_pinned_turn("cursor", {"success": True, "type": "cursor_command", "response": "x",
                                  "agent_id": "cursor", "query_id": "fq"}, latency_ms=1, db_path=db)
    assert outcomes.set_feedback_for_query("bad", "fq", db_path=db) == "pin-fq"
    assert outcomes.all_outcomes(db_path=db)[0]["user_feedback"] == "bad"
    outcomes.set_feedback_for_query(None, "fq", db_path=db)
    assert outcomes.all_outcomes(db_path=db)[0]["user_feedback"] is None
    assert outcomes.set_feedback_for_query("good", "missing", db_path=db) is None


def _pin(db, qid, agent, model, *, effort=None, ok=True, feedback=None, latency=1000):
    record_pinned_turn(agent, {
        "success": True, "type": f"{agent}_command" if ok else f"{agent}_error",
        "response": "ok" if ok else "[FAIL] exited with code 2",
        "agent_id": agent, "agent_model": model, "agent_effort": effort, "query_id": qid,
    }, latency_ms=latency, db_path=db)
    if feedback:
        outcomes.set_feedback_for_query(feedback, qid, db_path=db)


def test_performance_groups_per_agent_model_effort(tmp_path):
    db = tmp_path / "o.db"
    _pin(db, "a1", "codex", "gpt-6-luna", effort="high", latency=10_000)
    _pin(db, "a2", "codex", "gpt-6-luna", effort="high", latency=30_000, feedback="bad")
    _pin(db, "a3", "codex", "gpt-6-luna", effort="low", ok=False)
    _pin(db, "a4", "muse", "muse-spark-1.3", effort="medium")

    payload = service.cuttle_performance(db_path=db, days=0)
    rows = {r["id"]: r for r in payload["rows"]}
    assert set(rows) == {"codex|gpt-6-luna|high", "codex|gpt-6-luna|low", "muse|muse-spark-1.3|medium"}
    high = rows["codex|gpt-6-luna|high"]
    assert high["turns"] == 2
    assert high["success_rate"] == 100.0
    assert high["score"] == 50.0
    assert high["mean_duration_seconds"] == 20.0
    assert high["mean_total_duration_seconds"] == 20.0
    assert "mean_total_duration_seconds" in payload["axis_fields"]
    assert high["provider"] == "OpenAI" and high["harness"] == "codex"
    assert rows["codex|gpt-6-luna|low"]["score"] == 0.0
    assert payload["stats"]["pinned_turns"] == 4
    assert set(payload["filters"]["harnesses"]) == {"codex", "muse"}
    assert len(payload["turns"]) == 4

    router_only = service.cuttle_performance(db_path=db, days=0, source_id="router")
    assert router_only["rows"] == []


# ── Jev turn labels ───────────────────────────────────────────────────────

from api.jev.client import FakeJevClient
from api.jev import labels as jev_labels
from api.agent_router.pinned_outcomes import turn_contexts


def _counting_client(noul_for=lambda turn: 0.9):
    calls = []

    def handler(state, questions):
        calls.append(state)
        answers = {}
        for t in state["turns"]:
            answers[f"accept_{t['id']}"] = {"type": "noul", "noul": noul_for(t)}
            answers[f"miss_{t['id']}"] = {"type": "choice", "choice": "context", "confidence": 0.7}
        return answers

    return FakeJevClient(handler=handler), calls


def _row(did, **kw):
    return {"decision_id": did, "attempt_index": 0, "success": True, "failure_kind": "none",
            "target_agent": "cursor", "target_model": "auto", **kw}


def test_rule_labels_skip_jev_calls():
    client, calls = _counting_client()
    rows = [
        _row("thumb", user_feedback="bad"),
        _row("fail", success=False, failure_kind="task"),
        _row("cancel", success=False, failure_kind="cancelled"),
        _row("net", success=False, failure_kind="transport"),
        _row("ok1"),
        _row("ok2"),
    ]
    out = {r["decision_id"]: r["jev"] for r in jev_labels.label_rows(rows, client=client)}
    assert len(calls) == 1 and len(calls[0]["turns"]) == 2
    assert out["thumb"]["accepted"] is False and out["thumb"]["source"] == "rule"
    assert out["net"]["miss_kind"] == "transport"
    assert out["cancel"]["accepted"] is None
    assert out["ok1"]["source"] == "jev" and out["ok1"]["accepted"] is True


def test_label_rows_batches_and_caches():
    client, calls = _counting_client()
    rows = [_row(f"d{i}") for i in range(25)]
    jev_labels.label_rows(rows, client=client, limit=100, batch_size=10)
    assert len(calls) == 3
    again = jev_labels.label_rows(rows, client=client, limit=100, batch_size=10)
    assert len(calls) == 3
    assert all(r["jev"]["source"] == "jev" for r in again)
    assert jev_labels.pending_rows(rows) == []


def test_unsure_band_and_reject_miss_kind():
    client, _ = _counting_client(lambda t: 0.5 if t["id"] == "t0" else 0.1)
    out = jev_labels.label_rows([_row("u"), _row("r")], client=client)
    assert out[0]["jev"]["accepted"] is None
    assert out[1]["jev"]["accepted"] is False and out[1]["jev"]["miss_kind"] == "context"


def test_turn_contexts_include_next_user_message(tmp_path):
    auth = tmp_path / "auth.db"
    _auth_db(auth, [
        {"id": 1, "role": "user", "ts": "2026-09-01 10:00:00",
         "content": "<cuttle_context>\nsecret\n</cuttle_context>\nfix the login bug"},
        {"id": 2, "role": "assistant", "ts": "2026-09-01 10:01:00", "content": "Patched auth.py",
         "meta": {"query_id": "q1"}},
        {"id": 3, "role": "user", "ts": "2026-09-01 10:03:00", "content": "still broken"},
        {"id": 4, "role": "assistant", "ts": "2026-09-01 10:04:00", "content": "Try now",
         "meta": {"query_id": "q2"}},
    ])
    ctx = turn_contexts([{"query_id": "q1", "session_id": 1}, {"query_id": "q2", "session_id": 1}],
                        auth_db_path=auth)
    assert ctx["q1"] == {"ask": "fix the login bug", "reply": "Patched auth.py",
                         "next_user": "still broken", "next_gap_s": 120}
    assert ctx["q2"]["next_user"] is None


def test_dashboard_uses_cached_jev_rejects(tmp_path):
    db = tmp_path / "o.db"
    _pin(db, "j1", "cursor", "auto")
    _pin(db, "j2", "cursor", "auto")
    client, _ = _counting_client(lambda t: 0.1)
    jev_labels.label_rows(outcomes.all_outcomes(db_path=db)[:1], client=client)
    payload = service.cuttle_performance(db_path=db, days=0)
    row = payload["rows"][0]
    assert row["score"] == 50.0 and row["success_rate"] == 100.0
    assert payload["stats"]["rejected_labels"] == 1


def test_follow_up_turns_ask_pushback_not_accept():
    seen = {}

    def handler(state, questions):
        seen["keys"] = sorted(questions)
        return {"pushback_t0": {"type": "noul", "noul": 0.9}, "accept_t1": {"type": "noul", "noul": 0.9}}

    rows = [_row("a", query_id="qa"), _row("b", query_id="qb")]
    ctx = {"qa": {"ask": "fix it", "reply": "done", "next_user": "still broken"},
           "qb": {"ask": "hi", "reply": "hello", "next_user": None}}
    res = jev_labels.label_batch(rows, ctx, client=FakeJevClient(handler=handler))
    assert "pushback_t0" in seen["keys"] and "accept_t1" in seen["keys"]
    assert res["labels"][0]["accepted"] is False and res["labels"][0]["had_follow_up"] is True
    assert res["labels"][1]["accepted"] is True
