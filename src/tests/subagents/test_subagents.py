"""Sub-agent child chats: spawn, collect modes, cancel, metadata, CLI."""

from __future__ import annotations

import io
import json
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from api.auth_db import AuthDatabase
from api.subagents import service
from api.subagents.spec import harness_remainder, parse_child, sticky_user_text
from api.subagents.types import MAX_CHILDREN, MAX_DEPTH


def _seed(tmp_path: Path):
    db_path = tmp_path / "cuttle_auth.db"
    db = AuthDatabase(db_path)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    parent = db.create_chat_session(owner, "Parent chat")
    db.add_message(
        parent,
        "user",
        "plan dinner",
        metadata={
            "slash_command": {
                "chips": [{
                    "label": "Cursor - Auto",
                    "meta": "/cursor · requested Auto",
                    "category": "cursor",
                }]
            }
        },
    )
    return db, db_path, owner, parent


def _runner_factory(replies: dict[str, str] | None = None):
    def runner(**kwargs):
        spec = kwargs.get("spec")
        title = spec.title if spec is not None else "child"
        text = (replies or {}).get(title) or f"reply from {title}"
        return {"success": True, "response": text}

    return runner


def test_create_subagent_session_nests(tmp_path: Path):
    db, _db_path, owner, parent = _seed(tmp_path)
    child = db.create_subagent_chat_session(
        owner, parent_session_id=parent, session_name="Chef A"
    )
    row = db.get_chat_session_by_id(child)
    assert row["origin"] == "subagent"
    assert int(row["parent_session_id"]) == parent
    assert row["session_name"] == "Chef A"
    assert db.session_nesting_depth(parent) == 0
    assert db.session_nesting_depth(child) == 1


def test_spec_mixed_harness_prefix():
    spec = parse_child(
        {
            "title": "C",
            "agent": "codex",
            "model": "gpt-5.6",
            "effort": "low",
            "message": "vote dinner",
        }
    )
    assert sticky_user_text(spec).startswith("/codex gpt-5.6 low")
    assert "vote dinner" in sticky_user_text(spec)
    assert harness_remainder(
        agent="cursor", model="grok-4.6", effort="low", message="hi"
    ).startswith("grok-4.6 low")


def test_spawn_wait_all_collects_replies(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    payload = service.spawn(
        parent_session_id=parent,
        children=[
            {"title": "Chef A", "agent": "cursor", "message": "eggs?"},
            {"title": "Chef B", "agent": "cursor", "model": "grok-4.6", "effort": "low", "message": "spicy?"},
            {"title": "Chef C", "agent": "codex", "message": "fast?"},
        ],
        collect="all",
        wait=True,
        timeout=8,
        runner=_runner_factory(),
        db=db,
    )
    assert payload["ok"] is True
    assert payload["collect"] == "all"
    kids = payload["children"]
    assert len(kids) == 3
    assert payload["status"] == "done"
    titles = {c["label"] for c in kids}
    assert titles == {"Chef A", "Chef B", "Chef C"}
    for child in kids:
        assert child["status"] == "done"
        assert child["handle"].startswith("CH-")
        assert "reply from" in (child.get("result") or "")
        row = db.get_chat_session_by_id(child["session_id"])
        assert int(row["parent_session_id"]) == parent
        assert row["origin"] == "subagent"
        msgs = db.get_messages(child["session_id"])
        roles = [m["role"] for m in msgs]
        assert "user" in roles and "assistant" in roles
        user_msg = next(m for m in msgs if m["role"] == "user")
        umeta = user_msg.get("metadata") or {}
        assert umeta.get("speaker") == "Cuttle"
        assert umeta.get("speaker_kind") == "parent"
        assert umeta.get("parent_handle", "").startswith("CH-")
        parent_chips = (umeta.get("slash_command") or {}).get("chips") or []
        assert parent_chips, umeta
        assert parent_chips[0]["category"] == "cursor"
        assert "Auto" in parent_chips[0]["label"]
        assert not str(user_msg.get("content") or "").startswith("/")
        asst_msg = next(m for m in msgs if m["role"] == "assistant")
        ameta = asst_msg.get("metadata") or {}
        assert ameta.get("speaker") == child["label"]
        asst_chips = (ameta.get("slash_command") or {}).get("chips") or []
        assert asst_chips, ameta
        if child["label"] == "Chef B":
            assert "grok" in asst_chips[0]["label"].lower() or "grok" in (
                asst_chips[0].get("meta") or ""
            ).lower()
            assert "low" in (asst_chips[0]["label"] + asst_chips[0].get("meta", "")).lower()
        if child["label"] == "Chef C":
            assert asst_chips[0]["category"] == "codex"


def test_collect_first_cancels_the_rest(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    release_slow = threading.Event()

    def runner(**kwargs):
        spec = kwargs["spec"]
        if spec.title != "Fast":
            release_slow.wait(5)
        return {"success": True, "response": f"from {spec.title}"}

    payload = service.spawn(
        parent_session_id=parent,
        children=[
            {"title": "Fast", "message": "a"},
            {"title": "Slow1", "message": "b"},
            {"title": "Slow2", "message": "c"},
        ],
        collect="first",
        wait=True,
        timeout=8,
        runner=runner,
        db=db,
    )
    release_slow.set()
    assert payload["ok"] is True
    statuses = {c["label"]: c["status"] for c in payload["children"]}
    assert statuses["Fast"] == "done"
    assert statuses["Slow1"] in ("cancelled", "done", "failed")
    assert statuses["Slow2"] in ("cancelled", "done", "failed")
    cancelled = sum(1 for s in statuses.values() if s == "cancelled")
    assert cancelled >= 1 or statuses["Fast"] == "done"


def test_collect_serial_order(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    order: list[str] = []
    lock = threading.Lock()

    def runner(**kwargs):
        with lock:
            order.append(kwargs["spec"].title)
        return {"success": True, "response": kwargs["spec"].title}

    payload = service.spawn(
        parent_session_id=parent,
        children=[
            {"title": "One", "message": "1"},
            {"title": "Two", "message": "2"},
            {"title": "Three", "message": "3"},
        ],
        collect="serial",
        wait=True,
        timeout=8,
        runner=runner,
        db=db,
    )
    assert payload["status"] == "done"
    assert order == ["One", "Two", "Three"]


def test_message_followup_and_cancel(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    spawned = service.spawn(
        parent_session_id=parent,
        children=[{"title": "Chatty", "message": "hello"}],
        collect="all",
        lifetime="conversational",
        wait=True,
        timeout=6,
        runner=_runner_factory({"Chatty": "first"}),
        db=db,
    )
    sid = spawned["children"][0]["session_id"]
    follow = service.message_child(
        sid,
        "and dessert?",
        wait=True,
        timeout=6,
        runner=_runner_factory({"Chatty": "second"}),
        db=db,
    )
    assert follow["ok"] is True
    assert "second" in (follow.get("response") or follow["child"].get("result") or "")
    cancelled = service.cancel_batch(spawned["id"], db=db)
    assert cancelled["status"] == "cancelled"


def test_depth_cap(tmp_path: Path):
    db, _db_path, owner, parent = _seed(tmp_path)
    current = parent
    last_ok = parent
    for i in range(MAX_DEPTH + 2):
        child = db.create_subagent_chat_session(
            owner, parent_session_id=current, session_name=f"L{i}"
        )
        last_ok = child
        current = child
    assert db.session_nesting_depth(last_ok) >= MAX_DEPTH
    with pytest.raises(service.SubagentError, match="nesting"):
        service.spawn(
            parent_session_id=last_ok,
            children=[{"title": "too deep", "message": "nope"}],
            wait=False,
            runner=_runner_factory(),
            db=db,
        )


def test_max_children(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    kids = [{"title": f"N{i}", "message": "x"} for i in range(MAX_CHILDREN + 1)]
    with pytest.raises(service.SubagentError, match="at most"):
        service.spawn(
            parent_session_id=parent,
            children=kids,
            wait=False,
            runner=_runner_factory(),
            db=db,
        )


def test_attach_metadata_launchers(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    spawned = service.spawn(
        parent_session_id=parent,
        children=[
            {"title": "A", "message": "a"},
            {"title": "B", "message": "b"},
        ],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    meta = service.attach_batches_to_assistant_meta(parent, {}, db=db)
    handles = {s["handle"] for s in meta["subagents"]}
    assert spawned["children"][0]["handle"] in handles
    assert spawned["children"][1]["handle"] in handles
    assert meta.get("subagent_batch_id") == spawned["id"]


def test_live_overlay_shape(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    spawned = service.spawn(
        parent_session_id=parent,
        children=[{"title": "Live", "message": "go"}],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    child_sid = spawned["children"][0]["session_id"]
    overlay = service.child_live_status(child_sid, db=db)
    assert overlay is not None
    assert overlay["subagent"]["handle"].startswith("CH-")
    parent_orbs = service.public_parent_subagents(parent, db=db)
    assert parent_orbs, "unattached finished children should still paint parent orbs"
    assert parent_orbs[0]["handle"].startswith("CH-")
    from api.subagents.store import bind_unattached_batches

    bind_unattached_batches(db, parent, 999)
    assert service.public_parent_subagents(parent, db=db) == []


def test_route_uses_router_decision(tmp_path: Path, monkeypatch):
    db, _db_path, _owner, parent = _seed(tmp_path)

    class _Target:
        agent = "muse"
        model = "muse-code"

    class _Decision:
        target = _Target()

    monkeypatch.setattr(
        "api.subagents.spec.decide_with_outcome",
        lambda ctx, config=None: (_Decision(), {}),
        raising=False,
    )

    def fake_apply(spec, **kwargs):
        from api.subagents.types import ChildSpec

        return ChildSpec(
            title=spec.title,
            message=spec.message,
            agent="muse",
            model="muse-code",
            effort=spec.effort,
            route=True,
        )

    monkeypatch.setattr("api.subagents.service.apply_router", fake_apply)
    payload = service.spawn(
        parent_session_id=parent,
        children=[{"title": "R", "message": "pick a harness", "route": True}],
        wait=True,
        timeout=6,
        route=True,
        runner=_runner_factory(),
        db=db,
    )
    assert payload["children"][0]["agent"] == "muse"
    assert payload["children"][0]["model"] == "muse-code"


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    from api.subagents.cli import main

    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def test_cli_spawn_status_json(tmp_path: Path, monkeypatch):
    db, db_path, _owner, parent = _seed(tmp_path)

    def fake_default_runner(**kwargs):
        return {"success": True, "response": "cli-ok"}

    monkeypatch.setattr("api.subagents.turns.default_runner", fake_default_runner)
    handle = f"CH-{parent:06d}"
    child_json = json.dumps(
        {"title": "CLI Chef", "agent": "cursor", "message": "dinner?"}
    )
    code, out, err = _run_cli(
        [
            "spawn",
            "--parent",
            handle,
            "--wait",
            "--json",
            "--db",
            str(db_path),
            "--child",
            child_json,
        ]
    )
    assert code == 0, err or out
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["children"][0]["label"] == "CLI Chef"
    code2, out2, err2 = _run_cli(
        ["status", "--parent", handle, "--json", "--db", str(db_path)]
    )
    assert code2 == 0, err2 or out2
    listed = json.loads(out2)
    assert listed["ok"] is True
    assert listed["batches"]


def test_builtin_and_ephemeral_profiles(tmp_path: Path):
    db, _db_path, owner, parent = _seed(tmp_path)
    scout = service.spawn(
        parent_session_id=parent,
        children=[{"profile": "scout", "message": "look around"}],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    kid = scout["children"][0]
    assert kid["label"] == "Scout"
    assert kid["avatar"] == "🔭"
    row = db.get_chat_session_by_id(kid["session_id"])
    assert row["display_name"] == "Scout"
    assert row["avatar"] == "🔭"
    assert row["agent_profile_id"] == "scout"
    muse = service.spawn(
        parent_session_id=parent,
        children=[{"profile": "muse", "message": "compose"}],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    assert muse["children"][0]["agent"] == "muse"
    eph = service.spawn(
        parent_session_id=parent,
        children=[
            {
                "profile": {"name": "Dinner Judge", "avatar": "⚖️", "agent": "codex"},
                "message": "pick a winner",
            }
        ],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    e = eph["children"][0]
    assert e["label"] == "Dinner Judge"
    assert e["avatar"] == "⚖️"
    assert e["agent"] == "codex"


def test_cli_profiles_roundtrip(tmp_path: Path):
    db, db_path, owner, _parent = _seed(tmp_path)
    code, out, err = _run_cli(["profiles", "list", "--json", "--db", str(db_path)])
    assert code == 0, err or out
    listed = json.loads(out)
    ids = {p["id"] for p in listed["profiles"]}
    assert "scout" in ids and "codex" in ids
    code, out, err = _run_cli(
        [
            "profiles",
            "save",
            "--id",
            "judge",
            "--name",
            "Judge",
            "--avatar",
            "⚖️",
            "--agent",
            "codex",
            "--json",
            "--db",
            str(db_path),
            "--user-id",
            str(owner),
        ]
    )
    assert code == 0, err or out
    saved = json.loads(out)
    assert saved["profile"]["id"] == "judge"
    assert saved["profile"]["agent"] == "codex"
    code, out, err = _run_cli(
        ["profiles", "get", "judge", "--json", "--db", str(db_path), "--user-id", str(owner)]
    )
    assert code == 0, err or out
    got = json.loads(out)
    assert got["profile"]["name"] == "Judge"
    code, out, err = _run_cli(
        [
            "profiles",
            "delete",
            "judge",
            "--json",
            "--db",
            str(db_path),
            "--user-id",
            str(owner),
        ]
    )
    assert code == 0, err or out
    from api.subagents.profiles import resolve

    assert resolve(db, owner, "judge") is None


def test_resolve_child_cursor_model_bakes_effort():
    from api.subagents.identity import resolve_child_model_id

    assert resolve_child_model_id("cursor", "grok-4.6", "low") == "cursor-grok-4.6-low"
    assert resolve_child_model_id("cursor", "cursor-grok-4.6-low", "low") == "cursor-grok-4.6-low"
    assert resolve_child_model_id("muse", "muse-spark-1.3-contributor", "") == (
        "muse-spark-1.3-contributor"
    )


def test_child_slash_command_includes_codex_effort():
    from api.subagents.identity import child_slash_command
    from api.subagents.types import ChildSpec

    sc = child_slash_command(
        ChildSpec(
            title="Luna",
            message="echo riddle",
            agent="codex",
            model="gpt-5.6",
            effort="low",
        )
    )
    chip = sc["chips"][0]
    assert chip["category"] == "codex"
    assert "low" in chip["label"].lower()
    assert "effort low" in chip["meta"].lower()


def test_agent_to_agent_badges_are_parent_then_child(tmp_path: Path):
    db, _db_path, _owner, parent = _seed(tmp_path)
    seen = {}

    def runner(**kwargs):
        seen.update(kwargs)
        return {"success": True, "response": "footsteps"}

    payload = service.spawn(
        parent_session_id=parent,
        children=[{
            "title": "Grok Riddler",
            "agent": "cursor",
            "model": "grok-4.6",
            "effort": "low",
            "message": "The more you take the more you leave behind",
        }],
        wait=True,
        timeout=6,
        runner=runner,
        db=db,
    )
    assert seen.get("model_override") == "cursor-grok-4.6-low"
    assert seen.get("prompt") == "The more you take the more you leave behind"
    sid = payload["children"][0]["session_id"]
    msgs = db.get_messages(sid)
    user_msg = next(m for m in msgs if m["role"] == "user")
    assert user_msg["content"].startswith("The more you take")
    assert not user_msg["content"].startswith("/cursor")
    uchips = (user_msg["metadata"]["slash_command"]["chips"])
    assert uchips[0]["category"] == "cursor"
    assert "Auto" in uchips[0]["label"]
    asst = next(m for m in msgs if m["role"] == "assistant")
    achips = asst["metadata"]["slash_command"]["chips"]
    blob = (achips[0]["label"] + " " + achips[0].get("meta", "")).lower()
    assert "grok" in blob
    assert "low" in blob
    assert asst["metadata"]["cursor_run"]["requested_model"] == "cursor-grok-4.6-low"


def test_hydrate_repairs_legacy_child_badges(tmp_path: Path):
    from api.subagents.identity import hydrate_subagent_message_badges

    db, _db_path, _owner, parent = _seed(tmp_path)
    payload = service.spawn(
        parent_session_id=parent,
        children=[{
            "title": "Muse Riddler",
            "agent": "muse",
            "model": "muse-spark-1.3-contributor",
            "message": "keyboard riddle",
        }],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    sid = payload["children"][0]["session_id"]
    msgs = db.get_messages(sid)
    user_msg = next(m for m in msgs if m["role"] == "user")
    asst = next(m for m in msgs if m["role"] == "assistant")
    # CH-000554: inbound already had Muse chips (from /muse body), assistant had none.
    user_msg["metadata"]["slash_command"] = {
        "chips": [{
            "label": "Muse Code - Spark 1.3 (contributor)",
            "meta": "/muse · model muse-spark-1.3-contributor",
            "category": "muse",
        }]
    }
    asst["metadata"].pop("slash_command", None)
    hydrate_subagent_message_badges(db, sid, [user_msg, asst])
    assert user_msg["metadata"]["slash_command"]["chips"][0]["category"] == "cursor"
    assert "Auto" in user_msg["metadata"]["slash_command"]["chips"][0]["label"]
    chips = asst["metadata"]["slash_command"]["chips"]
    assert chips[0]["category"] == "muse"
    assert "spark" in chips[0]["label"].lower() or "muse-spark" in chips[0]["meta"].lower()


def test_hydrate_overwrites_cursor_auto_assistant_with_child_model(tmp_path: Path):
    """CH-000555: assistant already badged Cursor Auto must become Grok 4.6 Low."""
    from api.subagents.identity import hydrate_subagent_message_badges

    db, _db_path, _owner, parent = _seed(tmp_path)
    payload = service.spawn(
        parent_session_id=parent,
        children=[{
            "title": "Grok Riddler",
            "agent": "cursor",
            "model": "grok-4.6",
            "effort": "low",
            "message": "footsteps riddle",
        }],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    sid = payload["children"][0]["session_id"]
    msgs = db.get_messages(sid)
    user_msg = next(m for m in msgs if m["role"] == "user")
    asst = next(m for m in msgs if m["role"] == "assistant")
    asst["metadata"]["slash_command"] = {
        "chips": [{
            "label": "Cursor - Auto",
            "meta": "/cursor · requested Auto",
            "category": "cursor",
        }]
    }
    asst["metadata"]["cursor_run"] = {"requested_model": "auto"}
    hydrate_subagent_message_badges(db, sid, [user_msg, asst])
    blob = (
        asst["metadata"]["slash_command"]["chips"][0]["label"]
        + " "
        + asst["metadata"]["slash_command"]["chips"][0].get("meta", "")
    ).lower()
    assert "grok" in blob
    assert "low" in blob
    assert asst["metadata"]["cursor_run"]["requested_model"] == "cursor-grok-4.6-low"


def test_hydrate_repairs_missing_codex_assistant_badge(tmp_path: Path):
    """CH-000556: Luna inbound showed Codex; assistant had no chip."""
    from api.subagents.identity import hydrate_subagent_message_badges

    db, _db_path, _owner, parent = _seed(tmp_path)
    payload = service.spawn(
        parent_session_id=parent,
        children=[{
            "title": "Luna Riddler",
            "agent": "codex",
            "model": "gpt-5.6",
            "effort": "low",
            "message": "echo riddle",
        }],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    sid = payload["children"][0]["session_id"]
    msgs = db.get_messages(sid)
    user_msg = next(m for m in msgs if m["role"] == "user")
    asst = next(m for m in msgs if m["role"] == "assistant")
    user_msg["metadata"]["slash_command"] = {
        "chips": [{
            "label": "Codex - GPT-5.6",
            "meta": "/codex · model gpt-5.6",
            "category": "codex",
        }]
    }
    asst["metadata"].pop("slash_command", None)
    hydrate_subagent_message_badges(db, sid, [user_msg, asst])
    assert user_msg["metadata"]["slash_command"]["chips"][0]["category"] == "cursor"
    assert "Auto" in user_msg["metadata"]["slash_command"]["chips"][0]["label"]
    chips = asst["metadata"]["slash_command"]["chips"]
    assert chips[0]["category"] == "codex"
    assert "low" in chips[0]["label"].lower()


def test_hub_rule_mentions_cli():
    root = Path(__file__).resolve().parents[3]
    text = (root / ".cuttle_global" / "rules" / "03-subagents.md").read_text(encoding="utf-8")
    assert "python -m api.subagents" in text
    assert "--collect" in text
    from api.cuttle_brain.context_compiler import load_global_rules

    names = [n for n, _ in load_global_rules()]
    assert any("03-subagents" in n for n in names)


def test_child_query_log_id_reaches_the_live_overlay(tmp_path: Path):
    """A running child pane needs the query id, not just a status string.

    Sub-agent turns execute in the `api.subagents` CLI process, so Flask's
    process-local live-status store never sees their `query_started` event. The
    id has to travel through the child row for the pane's query-log button to
    become inspectable mid-turn.
    """
    db, _db_path, _owner, parent = _seed(tmp_path)
    spawned = service.spawn(
        parent_session_id=parent,
        children=[{"title": "Logged", "message": "go"}],
        wait=True,
        timeout=6,
        runner=_runner_factory(),
        db=db,
    )
    child = spawned["children"][0]
    from api.subagents import store

    store.update_child(db, child["id"], query_id="abc12345")
    overlay = service.child_live_status(child["session_id"], db=db)
    assert overlay["query_id"] == "abc12345"
    assert overlay["report_url"] == "/query_log.html?id=abc12345"
    # No id yet -> no bogus report_url (a bare one navigated the panel away).
    store.update_child(db, child["id"], query_id="")
    assert service.child_live_status(child["session_id"], db=db)["report_url"] is None


def test_child_status_sink_persists_query_started(tmp_path: Path):
    """The kernel only emits `query_started` when a status_queue is supplied.

    `api.subagents` ran without one, so sub-agent panes had no query id for the
    whole turn. The sink stands in for a queue and pins the id to the row.
    """
    from api.subagents import store
    from api.subagents.turns import ChildStatusSink
    from api.subagents.types import ChildSpec

    db, _db_path, _owner, _parent = _seed(tmp_path)
    batch = store.insert_batch(
        db, parent_session_id=1, user_id=1, collect="all", lifetime="one_shot",
    )
    child = store.insert_child(
        db, batch_id=batch.id, session_id=1, sort_index=0,
        spec=ChildSpec(title="Sink", message="go"), prompt="go",
    )
    store.update_child(db, child.id, status="running", started=True)
    sink = ChildStatusSink(db, child.id)

    sink.put(("query_started", {"query_id": "feed1234", "report_url": "/x"}))
    assert sink.query_id == "feed1234"
    assert store.get_child(db, child.id).query_id == "feed1234"

    sink.put_nowait(("status", "Muse: reading files"))
    assert sink.last_status == "Muse: reading files"
    assert store.get_child(db, child.id).live_status == "Muse: reading files"

    # Noise and malformed items must never break the turn.
    sink.put(("query_started", {}))
    sink.put("not-a-tuple")
    sink.put(("query_started", None))
    assert store.get_child(db, child.id).query_id == "feed1234"
    sink.close()


def test_orphan_running_child_is_reconciled(tmp_path: Path, monkeypatch):
    """A child whose host process died must not report 'running' forever.

    `run_child_turn` runs inside the process that spawned it; when that host is
    SIGKILLed no cleanup runs, so the row kept saying `running` and the child
    pane spun with no agent behind it.
    """
    import os

    from api.subagents import store
    from api.subagents.types import ORPHAN_ERROR, ChildSpec

    db, _db_path, owner_id, parent = _seed(tmp_path)
    batch = store.insert_batch(
        db, parent_session_id=parent, user_id=owner_id,
        collect="all", lifetime="one_shot",
    )
    child_sid = db.create_subagent_chat_session(
        owner_id, parent_session_id=parent, session_name="Orphan",
    )
    child_id = store.insert_child(
        db,
        batch_id=batch.id,
        session_id=child_sid,
        sort_index=0,
        spec=ChildSpec(title="Orphan", message="go"),
        prompt="go",
    ).id

    # Liveness is stubbed: the repo's test guard blocks real cross-process
    # os.kill probes, and the point here is the reconcile decision.
    alive = {os.getpid()}
    monkeypatch.setattr(
        store, "_pid_alive", lambda pid: int(pid) in alive
    )

    # A live owner (this very process) is left alone.
    store.update_child(db, child_id, status="running", owner_pid=os.getpid())
    assert store.reconcile_orphan_children(db) == []
    assert store.get_child(db, child_id).status == "running"

    # A dead owner is flipped terminal by the next observation.
    alive.clear()
    store.update_child(db, child_id, owner_pid=999_999)
    assert store.reconcile_orphan_children(db) == [child_id]
    row = store.get_child(db, child_id)
    assert row.status == "failed"
    assert row.error == ORPHAN_ERROR

    overlay = service.child_live_status(child_sid, db=db)
    assert overlay["generating"] is False
    assert "failed" in overlay["status"]

    # The batch can now finalize instead of waiting out its whole timeout.
    assert service.wait_batch(
        batch.id, timeout=6, db=db, advance=False
    )["timed_out"] is False


def test_pid_alive_ignores_junk():
    import os

    from api.subagents.store import _pid_alive

    assert _pid_alive(None) is False
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False
    assert _pid_alive("not-a-pid") is False
    assert _pid_alive(os.getpid()) is True


def test_cancelled_child_is_never_reconciled_to_failed(tmp_path, monkeypatch):
    from api.subagents import store
    from api.subagents.types import ChildSpec

    db, _db_path, owner_id, parent = _seed(tmp_path)
    batch = store.insert_batch(
        db, parent_session_id=parent, user_id=owner_id,
        collect="all", lifetime="one_shot",
    )
    child_sid = db.create_subagent_chat_session(
        owner_id, parent_session_id=parent, session_name="Cancelled",
    )
    child_id = store.insert_child(
        db, batch_id=batch.id, session_id=child_sid, sort_index=0,
        spec=ChildSpec(title="Cancelled", message="go"), prompt="go",
    ).id
    monkeypatch.setattr(store, "_pid_alive", lambda pid: False)
    store.update_child(db, child_id, status="cancelled", finished=True)
    store.update_child(db, child_id, owner_pid=999_999)
    assert store.reconcile_orphan_children(db) == []
    assert store.get_child(db, child_id).status == "cancelled"


def test_run_child_turn_stamps_the_owning_process(tmp_path: Path):
    import os

    from api.subagents import store

    db, _db_path, _owner, parent = _seed(tmp_path)
    seen = {}

    def runner(**kwargs):
        seen["owner"] = store.get_child(db, kwargs["child"].id).owner_pid
        seen["query_id"] = store.get_child(db, kwargs["child"].id).query_id
        return {"success": True, "response": "ok", "query_id": "cafe1234"}

    service.spawn(
        parent_session_id=parent,
        children=[{"title": "Owner", "message": "go"}],
        wait=True,
        timeout=6,
        runner=runner,
        db=db,
    )
    assert seen["owner"] == os.getpid()
    assert seen["query_id"] == ""
