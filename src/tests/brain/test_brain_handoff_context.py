"""Brain handoff cursors, compaction resets, state cleanup, and context metrics."""

from __future__ import annotations

from api.agent_harness.types import AgentManifest, AgentResult


def _chat():
    from api.auth_db import get_auth_db

    db = get_auth_db()
    uid = db.create_user("brain@local", "Brain", "local", password="x")
    return db, db.create_chat_session(uid, "handoff")


def _stub_tracking(monkeypatch):
    monkeypatch.setattr("api.query_tracker.start_query_tracking", lambda *a, **k: "qid")
    monkeypatch.setattr("api.query_tracker.finish_query_tracking", lambda *a, **k: None)
    monkeypatch.setattr("api.query_tracker.get_query_tracker", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.unregister_execution", lambda *a, **k: None)


class _Adapter:
    """Fake CLI adapter: records prompts, optional meta handler / compaction."""

    def __init__(self, cwd, *, resume_id="native-1", meta_reply=None, compacted=False):
        self.cwd = str(cwd)
        self.resume_id = resume_id
        self.meta_reply = meta_reply
        self.compacted = compacted
        self.prompts = []

    def available(self):
        return True

    def resolve_cwd(self, project_path):
        return self.cwd

    def load_resume(self, cwd, chat_session_id):
        return self.resume_id

    def save_resume(self, cwd, chat_session_id, cli_session_id):
        pass

    def clear_resume(self, cwd, chat_session_id):
        pass

    def handle_meta(self, prompt, chat_session_id=None, model=None, cwd=None):
        if self.meta_reply and prompt.startswith("model"):
            return AgentResult(success=True, output=self.meta_reply)
        return None

    async def execute(self, prompt, **kwargs):
        self.prompts.append(prompt)
        meta = {"context_compacted": True} if self.compacted else {}
        return AgentResult(
            success=True, output="ok", session_id=self.resume_id,
            usage={"prompt_tokens": 1200, "context_tokens": 1200}, meta=meta,
        )


def _run(monkeypatch, agent_id, adapter, prompt, sid, *, resume=True):
    from api.agent_harness import kernel

    manifest = AgentManifest(
        id=agent_id, label=agent_id, slash=f"/{agent_id}", resume=resume,
        capabilities_inject="once_per_resume",
    )
    monkeypatch.setattr(kernel, "get_agent", lambda aid, project_path=None: (manifest, adapter))
    _stub_tracking(monkeypatch)
    return kernel.run_agent_web_command(agent_id, prompt, sid, project_path=adapter.cwd)


def test_handoff_sends_everything_since_target_last_turn():
    from api.cuttle_brain import handoff as ho

    db, sid = _chat()
    db.add_message(sid, "user", "/cursor plan the refactor")
    ho.record_last_agent(sid, "cursor")
    db.add_message(sid, "assistant", "cursor's own plan")
    for i in range(20):
        db.add_message(sid, "user", f"/codex step {i}")
        db.add_message(sid, "assistant", f"codex did step {i}")
    db.add_message(sid, "user", "/cursor review it")

    h = ho.build_handoff(sid, to_agent="cursor", current_prompt="review it")
    assert h is not None
    # All 20 missed codex turns (40 rows), not a fixed last-12 window.
    assert h.message_count == 40
    assert "codex did step 0" in h.text and "codex did step 19" in h.text
    # Its own earlier reply is not repeated, nor is the prompt being sent now.
    assert "cursor's own plan" not in h.text
    assert "review it" not in h.text


def test_handoff_none_when_target_caught_up():
    from api.cuttle_brain import handoff as ho

    db, sid = _chat()
    db.add_message(sid, "user", "/cursor one")
    ho.record_last_agent(sid, "cursor")
    db.add_message(sid, "assistant", "reply one")
    db.add_message(sid, "user", "/cursor two")
    assert ho.build_handoff(sid, to_agent="cursor", current_prompt="two") is None


def test_handoff_covers_plain_llm_turns_on_first_agent_turn():
    from api.cuttle_brain import handoff as ho

    db, sid = _chat()
    db.add_message(sid, "user", "what is a cuttlefish")
    db.add_message(sid, "assistant", "a cephalopod (plain LLM)")
    db.add_message(sid, "user", "/cursor write that down")
    h = ho.build_handoff(sid, to_agent="cursor", current_prompt="write that down")
    assert h is not None
    assert "plain LLM" in h.text
    assert "write that down" not in h.text


def test_handoff_budget_reports_omitted_rows(monkeypatch):
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_MAX_HANDOFF_CHARS", 300)
    db, sid = _chat()
    ho.record_last_agent(sid, "cursor", through_message_id=0)
    for i in range(10):
        db.add_message(sid, "user", f"question {i} " + "x" * 80)
    h = ho.build_handoff(sid, to_agent="codex")
    assert h is not None and h.message_count < 10
    assert "earlier messages not shown" in h.text
    assert f"CH-{sid:06d}" in h.text
    assert "question 9" in h.text  # newest kept


def test_handoff_truncation_only_reports_full_session_pointer():
    """Critical tail: a long newest row is cut but kept, with recovery pointer."""
    from api.cuttle_brain import handoff as ho

    db, sid = _chat()
    ho.record_last_agent(sid, "cursor", through_message_id=0)
    db.add_message(sid, "user", "short q")
    db.add_message(sid, "assistant", "short a")
    db.add_message(sid, "user", "TAIL-" + "x" * 5000)
    h = ho.build_handoff(sid, to_agent="codex")
    assert h is not None
    assert h.message_count == 3
    assert h.truncated_count == 1
    assert "truncated to 1500 chars" in h.text
    assert "TAIL-" in h.text  # newest (critical tail) kept, truncated in place
    assert "earlier messages not shown" not in h.text
    handle = f"CH-{sid:06d}"
    assert f"python -m api.chat_cli session {handle} --all --json --full" in h.text
    # The emitted recovery command must parse (session accepts --all/--json/--full).
    from api.chat_cli.cli import build_parser

    args = build_parser().parse_args(
        ["session", handle, "--all", "--json", "--full"]
    )
    assert (args.command, args.all, args.json, args.full) == ("session", True, True, True)


def test_handoff_truncation_boundary_1500_1501():
    from api.cuttle_brain import handoff as ho

    kept, omitted, truncated = ho._fit_budget(
        [{"id": 1, "role": "user", "content": "y" * 1500}]
    )
    assert (omitted, truncated) == (0, 0)
    assert kept[0]["content"] == "y" * 1500
    kept, omitted, truncated = ho._fit_budget(
        [{"id": 1, "role": "user", "content": "y" * 1501}]
    )
    assert truncated == 1
    assert len(kept[0]["content"]) <= ho._MAX_MESSAGE_CHARS


def test_handoff_truncation_counts_kept_only_with_omitted(monkeypatch):
    """Budget-dropped long rows count as omitted, with a single combined pointer."""
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_MAX_HANDOFF_CHARS", 300)
    msgs = [{"id": i, "role": "user", "content": "L" + "z" * 2000} for i in range(5)]
    kept, omitted, truncated = ho._fit_budget(msgs)
    assert omitted > 0
    assert truncated == len(kept) < 5
    text = ho.format_handoff_delta(
        from_agent="a",
        to_agent="b",
        messages=kept,
        omitted=omitted,
        truncated=truncated,
        chat_handle="CH-000009",
    )
    assert "earlier messages not shown" in text
    assert "truncated to 1500 chars" in text
    assert text.count("python -m api.chat_cli session") == 1
    assert "session CH-000009 --all --json --full" in text
    assert "python -m api.chat_cli get CH-000009 --json`" not in text


def test_handoff_no_loss_has_no_pointer():
    from api.cuttle_brain import handoff as ho

    kept, omitted, truncated = ho._fit_budget(
        [{"id": 1, "role": "user", "content": "hi"}]
    )
    assert (omitted, truncated) == (0, 0)
    text = ho.format_handoff_delta(
        from_agent="a",
        to_agent="b",
        messages=kept,
        omitted=omitted,
        truncated=truncated,
        chat_handle="CH-000001",
    )
    assert "truncated" not in text
    assert "not shown" not in text
    assert "chat_cli" not in text


def test_no_resume_agent_gets_history_every_turn(tmp_path, monkeypatch):
    db, sid = _chat()
    db.add_message(sid, "user", "/deepseek name a color")
    db.add_message(sid, "assistant", "teal")
    db.add_message(sid, "user", "/deepseek do that again")
    adapter = _Adapter(tmp_path, resume_id=None)
    _run(monkeypatch, "deepseek", adapter, "do that again", sid, resume=False)
    db.add_message(sid, "assistant", "teal again")
    db.add_message(sid, "user", "/deepseek and once more")
    _run(monkeypatch, "deepseek", adapter, "and once more", sid, resume=False)
    first, second = adapter.prompts
    assert "Conversation so far" in first and "teal" in first
    # Same agent, next turn: still gets history (no native memory).
    assert "Conversation so far" in second and "teal again" in second
    assert second.count("and once more") == 1


def test_meta_command_does_not_move_cursor(tmp_path, monkeypatch):
    from api.cuttle_brain import handoff as ho

    db, sid = _chat()
    ho.record_last_agent(sid, "codex", through_message_id=0)
    db.add_message(sid, "user", "/cursor hello")
    ho.record_last_agent(sid, "cursor")
    db.add_message(sid, "assistant", "cursor hi")
    db.add_message(sid, "user", "/codex model gpt-x")
    _run(monkeypatch, "codex", _Adapter(tmp_path, meta_reply="model set"), "model gpt-x", sid)
    assert ho.get_last_agent(sid) == "cursor"
    assert ho.get_seen_cursor(sid, "codex") == 0
    db.add_message(sid, "assistant", "model set")
    db.add_message(sid, "user", "/codex build it")
    adapter = _Adapter(tmp_path)
    _run(monkeypatch, "codex", adapter, "build it", sid)
    assert "cursor hi" in adapter.prompts[0]


def test_compaction_drops_receipt_so_next_turn_is_full(tmp_path, monkeypatch):
    from api.cuttle_brain import context_delta as cd

    db, sid = _chat()
    db.add_message(sid, "user", "/codex start")
    _run(monkeypatch, "codex", _Adapter(tmp_path), "start", sid)
    assert cd.load_injected_snapshot(str(sid), "codex", str(tmp_path)) is not None
    db.add_message(sid, "user", "/codex long work")
    _run(monkeypatch, "codex", _Adapter(tmp_path, compacted=True), "long work", sid)
    assert cd.load_injected_snapshot(str(sid), "codex", str(tmp_path)) is None
    db.add_message(sid, "user", "/codex next")
    adapter = _Adapter(tmp_path)
    _run(monkeypatch, "codex", adapter, "next", sid)
    assert "<cuttle_context>" in adapter.prompts[0]
    assert "## Cuttle global rules" in adapter.prompts[0] or "## Profile" in adapter.prompts[0]


def test_manual_compact_clears_receipt(tmp_path, monkeypatch):
    from api import agent_context as ac
    from api.cuttle_brain import context_delta as cd

    cd.record_injected_snapshot("77", "cursor", str(tmp_path))
    cd.record_injected_snapshot("77", "codex", str(tmp_path))
    monkeypatch.setattr(ac, "_load_resume_id", lambda *a, **k: "rid")
    monkeypatch.setattr(ac, "_compact_cursor", lambda *a, **k: {"success": True})
    monkeypatch.setattr(ac, "get_agent_context_status", lambda **k: {})
    out = ac.compact_agent_context(chat_session_id="db_session_77", agent_id="cursor", cwd=str(tmp_path))
    assert out["success"]
    assert cd.load_injected_snapshot("77", "cursor", str(tmp_path)) is None
    assert cd.load_injected_snapshot("77", "codex", str(tmp_path)) is not None


def test_forget_chat_and_prune(tmp_path):
    from api.cuttle_brain import context_delta as cd
    from api.cuttle_brain import handoff as ho
    from api.cuttle_brain.state import forget_chat, prune

    cd.record_injected_snapshot("5", "cursor", str(tmp_path))
    cd.record_injected_snapshot("6", "cursor", str(tmp_path))
    ho.record_last_agent("5", "cursor", through_message_id=1)
    forget_chat(5)
    assert cd.load_injected_snapshot("5", "cursor", str(tmp_path)) is None
    assert ho.get_last_agent("5") is None

    junk = tmp_path / "pytest-of-x" / "t"
    junk.mkdir(parents=True)
    cd.record_injected_snapshot("muse-badge-session", "muse", str(junk))
    cd.record_injected_snapshot("discord_", "cursor", "/srv/guest-project")
    ho.record_last_agent("9", "codex", through_message_id=1)
    out = prune(live_ids={6})
    assert out["snapshots_removed"] == 1 and out["handoff_removed"] == 1
    assert cd.load_injected_snapshot("6", "cursor", str(tmp_path)) is not None
    assert cd.load_injected_snapshot("discord_", "cursor", "/srv/guest-project") is not None


def test_turn_records_context_metrics(tmp_path, monkeypatch):
    from api.cuttle_brain.metrics import fetch_turns

    db, sid = _chat()
    db.add_message(sid, "user", "/codex hi")
    _run(monkeypatch, "codex", _Adapter(tmp_path), "hi", sid)
    rows = fetch_turns(since_ts=0)
    assert len(rows) == 1
    row = rows[0]
    assert row["agent_id"] == "codex" and row["mode"] == "full"
    assert row["envelope_chars"] and row["layer_chars"].get("core_contract")
    assert row["context_tokens"] == 1200


def test_tasks_digest_reaches_runtime_block(monkeypatch, tmp_path):
    from api.cuttle_brain.context_compiler import compile_context

    db, sid = _chat()
    monkeypatch.setattr("api.chat_widgets.format_tasks_digest", lambda widgets: "## Active Tasks\n- [ ] ship it")
    compiled = compile_context("hi", project_path=str(tmp_path), chat_session_id=str(sid))
    assert "ship it" in compiled.envelope


def test_backfill_from_query_logs(tmp_path):
    import json

    from api.cuttle_brain.metrics import backfill_from_query_logs, fetch_turns

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "query_data_abc.json").write_text(json.dumps({
        "query_id": "abc", "timestamp": "2026-10-01T10:00:00", "success": True,
        "brain": {"mode": "full", "envelope_chars": 1000, "prompt_chars": 1100},
        "harness": {"agent_id": "cursor", "chat_session_id": "5", "cwd": "/p"},
    }), encoding="utf-8")
    (logs / "query_data_abc_20261001_100000.json").write_text("{}", encoding="utf-8")
    assert backfill_from_query_logs(logs)["inserted"] == 1
    assert backfill_from_query_logs(logs)["inserted"] == 0
    assert fetch_turns(since_ts=0)[0]["source"] == "backfill"


def test_context_dashboard_payload():
    import time

    from api.cuttle_brain.metrics import record_turn
    from api.dashboards.catalog import get_dashboard
    from api.dashboards.context import cuttle_context

    now = time.time()
    record_turn(ts=now - 60, chat_session_id="5", agent_id="codex", mode="full",
                envelope_chars=8000, layer_chars={"global_rules": 5000, "core_contract": 3000},
                context_tokens=20000, context_limit=200000, query_id="q1")
    record_turn(ts=now - 30, chat_session_id="5", agent_id="codex", mode="resume",
                context_tokens=60000, context_limit=200000, compacted=True, query_id="q2")
    record_turn(ts=now, chat_session_id="5", agent_id="codex", mode="full",
                full_reason="missing_snapshot", envelope_chars=8100, query_id="q3")
    assert get_dashboard("cuttle-context")
    p = cuttle_context(range_id="7d")
    assert p["stats"]["turns"] == 3 and p["stats"]["full"] == 2
    assert p["stats"]["compactions"] == 1 and p["stats"]["median_fill_pct"] == 20.0
    assert p["full_reasons"] == [{"id": "missing_snapshot", "count": 1}]
    assert {l["id"] for l in p["layers"]} == {"global_rules", "core_contract"}
    sess = p["sessions"][0]
    assert sess["handle"] == "CH-000005" and [pt["compacted"] for pt in sess["points"]] == [False, True]
    assert any(r["name"] == "00-safety.md" for r in p["rules"])


def test_context_dashboard_counts_turns_across_utc_midnight(monkeypatch):
    """Day axis and buckets share one zone: an evening turn in UTC-7 is still today."""
    import time

    from api.cuttle_brain.metrics import record_turn
    from api.dashboards import usage
    from api.dashboards.context import cuttle_context
    from datetime import timedelta, timezone

    monkeypatch.setattr(usage, "_tz", lambda off: timezone(timedelta(hours=-7)) if off is None
                        else timezone(timedelta(minutes=-int(off))))
    record_turn(ts=time.time(), chat_session_id="9", agent_id="codex", mode="full",
                envelope_chars=100, query_id="tz1")
    for off in (None, 420, -540, 0):
        p = cuttle_context(range_id="7d", tz_offset_minutes=off)
        assert p["stats"]["full"] == 1 and sum(p["modes"][0]["series"] + p["modes"][1]["series"]) >= 1, off
