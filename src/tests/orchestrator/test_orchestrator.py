"""Orchestrator playbooks, Agents profiles, spawn pin preflight, and wiring."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from api.experimental import flags
from api.orchestrator import model, service, store

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"


@pytest.fixture()
def orch(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "orchestrator.db")
    flags.set_enabled("orchestrator", True)
    yield
    flags.set_enabled("orchestrator", False)


def _seed_db(tmp_path):
    from api.auth_db import AuthDatabase
    from api.subagents.profiles import save_profile

    db = AuthDatabase(tmp_path / "auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    other = db.create_user("other@local", "Other", "local", password="x")
    sid = db.create_chat_session(owner, "orchestrator")
    other_sid = db.create_chat_session(other, "theirs")
    for pid, agent, mdl, effort in (
        ("grunt", "cursor", "auto", ""),
        ("grunt-muse", "muse", "muse-spark-1.3-contributor", ""),
        ("escalate", "cursor", "grok-4.6", ""),
        ("expert-claude", "claude", "", "high"),
        ("expert-codex", "codex", "gpt-6.1-sol", "high"),
    ):
        save_profile(db, owner, profile_id=pid, name=pid.title(), agent=agent, model=mdl, effort=effort)
    return db, owner, other, sid, other_sid


# ── pure model ─────────────────────────────────────────────────────────────


def test_default_playbook_normalizes_and_lists_profiles():
    pb = model.default_playbook()
    assert pb["schema"] == 1 and [t["role"] for t in pb["tiers"]] == ["worker", "escalation", "expert"]
    assert model.referenced_profiles(pb) == ["grunt", "grunt-muse", "escalate", "expert-claude", "expert-codex"]
    assert model.missing_profiles(pb, ["grunt"]) == ["grunt-muse", "escalate", "expert-claude", "expert-codex"]


@pytest.mark.parametrize("raw,needle", [
    ({"id": "1bad", "tiers": [{"role": "worker", "profiles": ["a"]}]}, "playbook id"),
    ({"id": "x", "tiers": []}, "at least one tier"),
    ({"id": "x", "tiers": [{"role": "expert", "profiles": ["a"]}]}, "worker tier"),
    ({"id": "x", "tiers": [{"id": "a", "role": "worker", "profiles": ["p"]},
                           {"id": "a", "role": "expert", "profiles": ["p"]}]}, "duplicated"),
    ({"id": "x", "tiers": [{"role": "worker", "profiles": []}]}, "at least one agent profile"),
    ({"id": "x", "tiers": [{"role": "boss", "profiles": ["a"]}]}, "role must be"),
    ({"id": "x", "tiers": [{"role": "worker", "profiles": ["a"]}], "limits": {"solo_minutes": 1}}, "solo_minutes"),
    ({"id": "x", "tiers": [{"role": "worker", "profiles": ["a"]}], "supervisor": {"every_sec": 5}}, "every_sec"),
    ({"id": "x", "tiers": [{"role": "worker", "profiles": ["a"]}],
      "supervisor": {"forbid_branches": ["bad branch;rm"]}}, "forbid_branches"),
])
def test_normalize_rejects_bad_documents(raw, needle):
    with pytest.raises(model.PlaybookError, match=needle):
        model.normalize_playbook(raw)


def test_normalize_drops_unknown_keys_and_parses_branch_text():
    pb = model.normalize_playbook({
        "id": "Galaxy Run", "evil": 1,
        "tiers": [{"role": "worker", "profiles": ["grunt", "grunt", ""]}],
        "supervisor": {"forbid_branches": "master, dev/core", "agent": "Cursor"},
    })
    assert pb["id"] == "galaxy-run" and "evil" not in pb
    assert pb["tiers"][0]["profiles"] == ["grunt"]
    assert pb["supervisor"]["forbid_branches"] == ["master", "dev/core"]
    assert pb["supervisor"]["agent"] == "cursor"


def test_render_brief_carries_limits_rules_and_pins():
    pb = model.normalize_playbook({**model.default_playbook(),
                                   "limits": {"solo_attempts": 3, "solo_minutes": 25, "setup_retries": 1},
                                   "notes": "Stay on Galaxy."})
    profiles = {"grunt": {"agent": "cursor", "model": "auto"},
                "expert-claude": {"agent": "claude", "model": "claude-opus-5-5", "effort": "high"}}
    text = model.render_brief(pb, profiles, chat_handle="CH-000042", goal="ship it")
    assert "CH-000042" in text and "Goal: ship it" in text
    assert "After 3 failed attempts or about 25 minutes" in text
    assert "After 1 setup failures" in text
    assert "never send `items`" in text
    assert "`grunt`: cursor auto" in text and "claude claude-opus-5-5 (high)" in text
    assert "(missing profile)" in text  # grunt-muse not supplied
    assert "Stay on Galaxy." in text
    assert model.render_brief(pb, profiles) == model.render_brief(pb, profiles)


def test_supervisor_argv_follows_chip_and_carries_guards():
    pb = model.normalize_playbook({**model.default_playbook(), "supervisor": {
        "require_branch": "dev/multiplayer", "require_path": "/w/galaxy",
        "forbid_branches": ["master", "dev/core"]}})
    argv = model.supervisor_argv(pb, "CH-001548", {"grunt": {"agent": "cursor", "model": "auto"}})
    assert argv[:4] == ["supervise", "CH-001548", "--every", "900"]
    assert "--agent" not in argv
    assert argv[argv.index("--require-branch") + 1] == "dev/multiplayer"
    assert argv[argv.index("--require-path") + 1] == "/w/galaxy"
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--forbid-branch"] == ["master", "dev/core"]
    tiers = json.loads(argv[argv.index("--model-tiers") + 1])
    assert tiers["worker"][0] == "cursor auto"
    pinned = model.normalize_playbook({**pb, "supervisor": {**pb["supervisor"], "agent": "codex"}})
    assert model.supervisor_argv(pinned, "CH-1", {})[4:6] == ["--agent", "codex"]


# ── service ────────────────────────────────────────────────────────────────


def test_seed_is_virtual_until_saved(tmp_path, orch):
    db, owner, *_ = _seed_db(tmp_path)
    listed = service.list_playbooks(owner, db=db)
    assert listed[0]["id"] == "default" and listed[0]["seed"] and listed[0]["missing_profiles"] == []
    assert store.list_playbooks(owner) == []
    saved = service.save_playbook(owner, model.default_playbook(), db=db)
    assert not saved.get("seed") and store.get_playbook(owner, "default")


def test_save_rejects_unknown_profiles(tmp_path, orch):
    db, owner, *_ = _seed_db(tmp_path)
    bad = {"id": "x", "tiers": [{"role": "worker", "profiles": ["grunt", "ghost"]}]}
    with pytest.raises(service.OrchestratorError, match="ghost"):
        service.save_playbook(owner, bad, db=db)


def test_bind_brief_and_context_block(tmp_path, orch, monkeypatch):
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    out = service.bind(f"CH-{sid:06d}", "default", "finish the slice", db=db)
    assert out["bound"] and out["goal"] == "finish the slice"
    assert f"CH-{sid:06d}" in out["brief"] and "--agent" not in out["supervisor_argv"]
    block = service.brief_block(f"db_session_{sid}")
    assert block.startswith("## Orchestrator playbook") and "finish the slice" in block
    assert service.brief_block(other_sid) == ""  # unbound
    with pytest.raises(service.OrchestratorError, match="another account"):
        service.bind(other_sid, "default", db=db, user_id=owner)
    flags.set_enabled("orchestrator", False)
    assert service.brief_block(sid) == ""  # flag off withdraws the brief
    flags.set_enabled("orchestrator", True)
    assert service.unbind(sid, db=db) and service.brief_block(sid) == ""


def test_compiled_context_carries_brief_for_bound_chat(tmp_path, orch, monkeypatch):
    db, owner, _other, sid, _ = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    service.bind(sid, "default", "", db=db)
    from api.cuttle_brain.context_compiler import _runtime_block

    text = _runtime_block(inventory={}, chat_session_id=f"CH-{sid:06d}", project_path=None)
    assert "## Orchestrator playbook: Default org chart" in text


def test_cli_start_and_brief(tmp_path, orch, monkeypatch):
    db, owner, _other, sid, _ = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setenv("CUTTLE_CHAT_SESSION_ID", str(sid))
    from api.orchestrator.__main__ import main

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert main(["start", "--goal", "g", "--json"]) == 0
    assert json.loads(buf.getvalue())["bound"]
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert main(["supervise-command", f"CH-{sid:06d}", "--json"]) == 0
    assert json.loads(buf.getvalue())["command"].startswith("python -m api.chat_cli supervise")
    flags.set_enabled("orchestrator", False)
    with redirect_stdout(io.StringIO()):
        assert main(["list", "--json"]) == 2


# ── profiles + spawn preflight ─────────────────────────────────────────────


def test_profile_save_validates_pins(tmp_path):
    from api.auth_db import AuthDatabase
    from api.subagents.profiles import save_profile

    db = AuthDatabase(tmp_path / "auth.db")
    owner = db.create_user("o@local", "O", "local", password="x")
    row = save_profile(db, owner, profile_id="opus", name="Opus", agent="claude", effort="high")
    assert row["agent"] == "claude"
    with pytest.raises(ValueError, match="Unsupported model"):
        save_profile(db, owner, profile_id="x", name="X", agent="cursor", model="no-such-model")
    with pytest.raises(ValueError, match="Unsupported effort"):
        save_profile(db, owner, profile_id="x", name="X", agent="cursor", model="auto", effort="high")
    with pytest.raises(ValueError, match="Unknown agent"):
        save_profile(db, owner, profile_id="x", name="X", agent="nope")
    with pytest.raises(ValueError, match="no harness"):
        save_profile(db, owner, profile_id="x", name="X", agent="none", model="auto")


def test_spawn_rejects_bad_pin_before_creating_children(tmp_path):
    from api.auth_db import AuthDatabase
    from api.subagents import service as sub
    from api.subagents.service import SubagentError

    db = AuthDatabase(tmp_path / "auth.db")
    owner = db.create_user("o@local", "O", "local", password="x")
    parent = db.create_chat_session(owner, "Parent")
    before = len(db.get_user_chat_sessions(owner)) if hasattr(db, "get_user_chat_sessions") else None
    with pytest.raises(SubagentError, match=r"child 1 \(Bad\).*No chat was created"):
        sub.spawn(parent_session_id=parent, db=db, runner=lambda **k: {"success": True, "response": "x"},
                  children=[{"title": "Bad", "agent": "codex", "model": "gpt-nope", "message": "hi"}])
    if before is not None:
        assert len(db.get_user_chat_sessions(owner)) == before


# ── routes ─────────────────────────────────────────────────────────────────


def _client(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client

    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    return ctx


def test_routes_disabled_when_flag_off(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)["client"]
    flags.set_enabled("orchestrator", False)
    for path in ("/api/orchestrator/playbooks", "/api/agent-profiles",
                 "/orchestrator_page.html", "/agent_profiles_page.html"):
        resp = client.get(path)
        assert resp.status_code == 403, path
    assert client.put("/api/agent-profiles/x", json={"name": "X"}).status_code == 403


def test_routes_round_trip(tmp_path, monkeypatch, orch):
    ctx = _client(tmp_path, monkeypatch)
    client = ctx["client"]
    put = client.put("/api/agent-profiles/worker", json={"name": "Worker", "agent": "cursor", "model": "auto"})
    assert put.status_code == 200 and put.get_json()["profile"]["agent"] == "cursor"
    bad = client.put("/api/agent-profiles/bad", json={"name": "Bad", "agent": "cursor", "model": "auto", "effort": "max"})
    assert bad.status_code == 400 and "effort" in bad.get_json()["error"]
    chk = client.post("/api/agent-profiles/check", json={"agent": "claude", "model": "", "effort": "high"}).get_json()
    assert chk["ok"] is True
    assert client.delete("/api/agent-profiles/scout").status_code == 400  # built-in
    pb = {"name": "Mine", "tiers": [{"id": "w", "role": "worker", "profiles": ["worker"]}]}
    saved = client.put("/api/orchestrator/playbooks/mine", json=pb)
    assert saved.status_code == 200, saved.get_json()
    ghost = client.put("/api/orchestrator/playbooks/ghost",
                       json={"tiers": [{"role": "worker", "profiles": ["ghost"]}]})
    assert ghost.status_code == 400
    preview = client.get(f"/api/orchestrator/playbooks/mine/preview?session=CH-{ctx['sid']:06d}").get_json()
    assert preview["success"] and f"CH-{ctx['sid']:06d}" in preview["supervisor_command"]
    other = client.get(f"/api/orchestrator/playbooks/mine/preview?session={ctx['other_sid']}")
    assert other.status_code == 400
    assert client.get("/orchestrator_page.html").status_code == 200
    assert client.get("/agent_profiles_page.html").status_code == 200
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    assert client.get("/api/orchestrator/playbooks").status_code == 403


# ── client wiring + pure JS model ──────────────────────────────────────────


def test_rail_apps_registered_stashed_and_flag_gated():
    html = (WEB / "app_shell.html").read_text(encoding="utf-8")
    rail = (WEB / "js" / "shell" / "shell_rail.js").read_text(encoding="utf-8")
    for nav, page in (("nav-orchestrator", "/orchestrator_page.html"), ("nav-agents", "/agent_profiles_page.html")):
        assert f'id="{nav}"' in html and f'data-page="{page}"' in html
        assert f"'{nav}'" in rail.split("const CANONICAL_RAIL_ITEM_ORDER")[1].split("];")[0]
        assert f"'{nav}'" in rail.split("const DEFAULT_RAIL_HIDDEN =")[1].split(";")[0]
        assert f"'{nav}': 'orchestrator'" in rail
    for page in ("orchestrator_page.html", "agent_profiles_page.html"):
        text = (WEB / page).read_text(encoding="utf-8")
        assert text.index("orchestrator_model.js") < text.index("_page.js?v=")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_js_model_pins_and_playbook_problems():
    script = """
const assert = require('assert');
const M = require(%s);
const claude = {id:'claude', label:'Claude', supports_model_pin:true, supports_effort:true,
  models:['opus'], efforts:['low','high'], model_efforts:{'claude-opus-4-6':['low','high','max']}};
const cursor = {id:'cursor', supports_model_pin:true, supports_effort:false, models:['auto'], efforts:[]};
assert.deepEqual(M.pinOptions(claude, 'claude-opus-4-6').efforts, ['low','high','max']);
assert.deepEqual(M.pinOptions(claude, '').efforts, ['low','high']);
assert.equal(M.pinOptions(cursor, 'auto').supportsEffort, false);
assert.equal(M.pinProblem(cursor, 'cursor', 'auto', 'high'), 'cursor has no effort levels for this model.');
assert.equal(M.pinProblem(null, 'none', '', ''), '');
assert.match(M.pinProblem(null, 'none', 'auto', ''), /Choose a harness/);
const pb = M.newPlaybook('x');
assert.ok(M.playbookProblems(pb, ['grunt']).some(p => /add at least one agent profile/.test(p)));
pb.tiers[0] = M.addProfileToTier(pb.tiers[0], 'grunt');
assert.deepEqual(M.playbookProblems(pb, ['grunt']), []);
pb.tiers.push(Object.assign(M.newTier(pb.tiers), {role:'expert', profiles:['ghost']}));
assert.ok(M.playbookProblems(pb, ['grunt']).some(p => /ghost/.test(p)));
assert.deepEqual(M.moveItem(['a','b','c'], 2, -1), ['a','c','b']);
assert.deepEqual(M.profileUsage([{name:'P', tiers:[{profiles:['a','b']}]}]), {a:['P'], b:['P']});
assert.deepEqual(M.parseBranches('master, dev/core  x'), ['master','dev/core','x']);
assert.equal(M.esc('<b>'), '&lt;b&gt;');
""" % json.dumps(str(WEB / "js" / "orchestrator" / "orchestrator_model.js"))
    subprocess.run(["node", "-e", script], check=True)
