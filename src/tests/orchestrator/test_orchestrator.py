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
    assert pb["schema"] == 2 and [t["role"] for t in pb["tiers"]] == ["worker", "escalation", "expert"]
    assert model.referenced_profiles(pb) == ["grunt", "grunt-muse", "escalate", "expert-claude", "expert-codex"]
    assert model.missing_profiles(pb, ["grunt"]) == ["grunt-muse", "escalate", "expert-claude", "expert-codex"]


@pytest.mark.parametrize("raw,needle", [
    ({"id": "1bad", "tiers": [{"role": "worker", "profiles": ["a"]}]}, "playbook id"),
    ({"id": "x", "tiers": []}, "at least one tier"),
    ({"id": "x", "tiers": [{"role": "expert", "profiles": ["a"]}]}, "worker tier"),
    ({"id": "x", "tiers": [{"id": "a", "role": "worker", "profiles": ["p"]},
                           {"id": "a", "role": "expert", "profiles": ["p"]}]}, "duplicated"),
    ({"id": "x", "tiers": [{"role": "worker", "profiles": []}]}, "at least one agent"),
    ({"id": "x", "tiers": [{"role": "worker", "members": [{"agent": "", "model": "auto"}]}]}, "needs a harness"),
    ({"id": "x", "tiers": [{"role": "worker", "members": [{"profile": "a", "on": False}]},
                           {"role": "expert", "members": ["b"]}]}, "switch on at least one agent"),
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
    assert pb["tiers"][0]["members"] == [{"profile": "grunt", "on": True}]
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
    assert "after 3 failed attempts or about 25 minutes without progress" in text
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


def test_members_toggle_and_temporary_agents_shape_brief_and_tiers():
    pb = model.normalize_playbook({"id": "mix", "tiers": [
        {"id": "worker", "role": "worker", "members": [
            {"profile": "grunt", "on": False},
            {"agent": "Cursor", "model": "auto"},
            {"agent": "cursor", "model": "auto"},  # duplicate pin dropped
        ]},
        {"id": "expert", "role": "expert", "members": [{"profile": "expert-claude", "on": False}]},
    ]})
    members = pb["tiers"][0]["members"]
    assert members == [{"profile": "grunt", "on": False},
                       {"name": "cursor auto", "agent": "cursor", "model": "auto", "effort": "", "on": True}]
    assert [t["id"] for t in model.active_tiers(pb)] == ["worker"]  # all-off tier is left out
    assert model.model_tiers(pb, {}) == {"worker": ["cursor auto"]}
    text = model.render_brief(pb, {})
    assert '`{"name":"cursor auto","agent":"cursor","model":"auto"}` (temporary): cursor auto' in text
    assert "`grunt`" not in text and "| Expert |" not in text
    assert model.referenced_profiles(pb) == ["grunt", "expert-claude"]  # off members still validated


def test_default_playbook_choice_drives_start(tmp_path, orch, monkeypatch):
    db, owner, _other, sid, _ = _seed_db(tmp_path)
    assert service.default_id(owner) == "default"
    fast = {"id": "fast", "name": "Fast", "tiers": [{"role": "worker", "members": ["grunt"]}]}
    service.save_playbook(owner, fast, db=db)
    with pytest.raises(service.OrchestratorError, match="not found"):
        service.set_default(owner, "ghost")
    assert service.set_default(owner, "fast") == "fast"
    listed = {p["id"]: p["is_default"] for p in service.list_playbooks(owner, db=db)}
    assert listed == {"default": False, "fast": True}
    assert service.bind(sid, "", "g", db=db)["playbook"]["id"] == "fast"
    assert service.bind(sid, "@default", "g", db=db)["playbook"]["id"] == "default"
    service.delete_playbook(owner, "fast")
    assert service.default_id(owner) == "default"  # stale pick falls back


def test_schema1_docs_read_as_members(tmp_path, orch):
    db, owner, *_ = _seed_db(tmp_path)
    store.put_playbook(owner, {"schema": 1, "id": "old", "name": "Old",
                               "tiers": [{"id": "w", "role": "worker", "profiles": ["grunt"]}]})
    doc = service.get_playbook(owner, "old", db=db)
    assert doc["schema"] == 2 and doc["tiers"][0]["members"] == [{"profile": "grunt", "on": True}]


def test_save_checks_temporary_agent_pins(tmp_path, orch, monkeypatch):
    db, owner, *_ = _seed_db(tmp_path)
    seen = []

    def fake_check(agent, model_id="", effort="", *, catalogs=None):
        seen.append((agent, model_id, effort))
        return "Effort max is not offered" if effort == "max" else None

    monkeypatch.setattr("api.agent_harness.agent_models.check_agent_pin", fake_check)
    ok = {"id": "t", "tiers": [{"role": "worker", "members": [{"agent": "muse-code", "effort": "high"}]}]}
    saved = service.save_playbook(owner, ok, db=db)
    assert saved["tiers"][0]["members"][0]["agent"] == "muse"  # harness alias normalized
    assert seen[-1] == ("muse", "", "high")
    bad = {"id": "t", "tiers": [{"role": "worker", "members": [{"agent": "claude", "effort": "max"}]}]}
    with pytest.raises(service.OrchestratorError, match="Effort max"):
        service.save_playbook(owner, bad, db=db)


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


def test_binding_titles_strip_leading_slash_commands():
    assert service._chat_title({"session_name": "/cursor /codex 🧪 Multiplayer"}) == "🧪 Multiplayer"
    assert service._chat_title({"session_name": "/restart"}) == "/restart"
    assert service._chat_title({}) == ""


def test_bind_brief_and_context_block(tmp_path, orch, monkeypatch):
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    out = service.bind(f"CH-{sid:06d}", "default", "finish the slice", db=db)
    assert out["bound"] and out["goal"] == "finish the slice"
    assert f"CH-{sid:06d}" in out["brief"] and "--agent" not in out["supervisor_argv"]
    block = service.brief_block(f"db_session_{sid}")
    assert block.startswith("## Orchestrator playbook") and "finish the slice" in block
    assert "Delegate hands-on implementation, investigation and tests from the start" in block
    assert "2 failed attempts or about 40 minutes without progress" in block
    assert "These limits apply to delegated work" in block
    assert "in your own turn" not in block
    assert service.brief_block(other_sid) == ""  # unbound
    with pytest.raises(service.OrchestratorError, match="another account"):
        service.bind(other_sid, "default", db=db, user_id=owner)
    flags.set_enabled("orchestrator", False)
    assert service.brief_block(sid) == ""  # flag off withdraws the brief
    flags.set_enabled("orchestrator", True)
    assert service.unbind(sid, db=db) and service.brief_block(sid) == ""


def test_mark_orchestrators_flags_bound_history_rows(tmp_path, orch):
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    service.bind(sid, "default", "", db=db)
    rows = [{"id": sid}, {"id": other_sid}, {"id": None}]
    service.mark_orchestrators(rows, owner)
    assert [r["orchestrator"] for r in rows] == [True, False, False]
    flags.set_enabled("orchestrator", False)
    service.mark_orchestrators(rows, owner)
    assert rows[0]["orchestrator"] is False
    flags.set_enabled("orchestrator", True)


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
    assert client.put("/api/orchestrator/default", json={"id": "mine"}).get_json()["default_id"] == "mine"
    assert client.get("/api/orchestrator/playbooks").get_json()["default_id"] == "mine"
    assert client.put("/api/orchestrator/default", json={"id": "ghost"}).status_code == 404
    preview = client.get(f"/api/orchestrator/playbooks/mine/preview?session=CH-{ctx['sid']:06d}").get_json()
    assert preview["success"] and f"CH-{ctx['sid']:06d}" in preview["supervisor_command"]
    other = client.get(f"/api/orchestrator/playbooks/mine/preview?session={ctx['other_sid']}")
    assert other.status_code == 400
    assert client.get("/orchestrator_page.html").status_code == 200
    assert client.get("/agent_profiles_page.html").status_code == 200
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    assert client.get("/api/orchestrator/playbooks").status_code == 403


def _png(w=40, h=20, color=(200, 30, 30, 255)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def test_avatar_normalize_and_store(tmp_path, monkeypatch):
    from PIL import Image
    from api.subagents import profile_avatars

    monkeypatch.setattr(profile_avatars, "output_dir", lambda: tmp_path)
    url = profile_avatars.store(7, _png(600, 300))
    assert url.startswith("/output/avatars/7/") and url.endswith(".png")
    saved = tmp_path / url[len("/output/"):]
    with Image.open(saved) as img:
        assert img.size == (256, 256)
    assert profile_avatars.store(7, _png(600, 300)) == url  # content-addressed
    for bad, needle in ((b"", "empty"), (b"not an image", "PNG, JPEG"), (b"\x89PNG\r\n\x1a\nbroken", "could not be read")):
        with pytest.raises(profile_avatars.AvatarError, match=needle):
            profile_avatars.normalize(bad)


def test_avatar_upload_route(tmp_path, monkeypatch, orch):
    from api.subagents import profile_avatars

    monkeypatch.setattr(profile_avatars, "output_dir", lambda: tmp_path)
    client = _client(tmp_path, monkeypatch)["client"]
    resp = client.post("/api/agent-profiles/avatar", data={"file": (io.BytesIO(_png()), "me.png")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_json()
    url = resp.get_json()["url"]
    assert url.startswith("/output/avatars/")
    bad = client.post("/api/agent-profiles/avatar", data={"file": (io.BytesIO(b"hello"), "x.png")},
                      content_type="multipart/form-data")
    assert bad.status_code == 400
    assert client.post("/api/agent-profiles/avatar").status_code == 400
    saved = client.put("/api/agent-profiles/pic", json={"name": "Pic", "avatar": url, "agent": "none"})
    assert saved.get_json()["profile"]["avatar"] == url


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
        assert text.index("page_controls.css") < text.index("orchestrator_page.css")  # page skin first
        assert "<datalist" not in text
    agents = (WEB / "agent_profiles_page.html").read_text(encoding="utf-8")
    # "Runs on" is the composer's chip picker, not a parallel set of dropdowns.
    for dep in ("searchable_select.js", "chat_chip_config.js", "agent_pin_picker.js", "chip_config_modal.css", "auth_modal.css"):
        assert dep in agents
    assert agents.index("chat_chip_config.js") < agents.index("agent_pin_picker.js")
    for html in ("app_shell.html", "chat_page.html"):
        assert "chip_config_modal.css" in (WEB / html).read_text(encoding="utf-8")


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
assert.ok(M.playbookProblems(pb, ['grunt']).some(p => /add at least one agent/.test(p)));
pb.tiers[0] = M.addMember(pb.tiers[0], 'grunt');
assert.deepEqual(M.playbookProblems(pb, ['grunt']), []);
pb.tiers[0] = M.toggleMember(pb.tiers[0], 0);
assert.ok(M.playbookProblems(pb, ['grunt']).some(p => /Switch on at least one agent/.test(p)));
const temp = M.temporaryMember([claude], {agent:'claude', model:'opus', effort:'high'});
assert.deepEqual(temp, {name:'Claude · opus · high', agent:'claude', model:'opus', effort:'high', on:true});
pb.tiers[0] = M.addMember(M.addMember(pb.tiers[0], temp), temp);  // duplicate pin ignored
assert.equal(pb.tiers[0].members.length, 2);
assert.deepEqual(M.playbookProblems(pb, ['grunt']), []);  // the temporary agent is on
pb.tiers[0] = M.promoteMember(pb.tiers[0], 1);
assert.ok(M.isTemporary(pb.tiers[0].members[0]) && !M.isTemporary(pb.tiers[0].members[1]));
pb.tiers[0] = M.replaceMember(pb.tiers[0], 0, Object.assign({}, temp, {effort:'low', on:false}));
assert.equal(pb.tiers[0].members[0].effort, 'low'); assert.equal(pb.tiers[0].members[0].on, true);  // keeps on/off
assert.equal(M.removeMember(pb.tiers[0], 0).members.length, 1);
pb.tiers.push(Object.assign(M.newTier(pb.tiers), {role:'expert', members:[{profile:'ghost'}]}));
assert.ok(M.playbookProblems(pb, ['grunt']).some(p => /ghost/.test(p)));
assert.deepEqual(M.tierMembers({profiles:['a']}), [{profile:'a', on:true}]);  // schema 1
const copy = M.duplicatePlaybook(Object.assign({seed:true, is_default:true}, pb, {id:'fast', name:'Fast'}), ['fast', 'fast-copy']);
assert.equal(copy.id, 'fast-copy-2'); assert.equal(copy.name, 'Fast (copy)');
assert.ok(!('seed' in copy) && !('is_default' in copy));
assert.equal(M.duplicatePlaybook(copy, []).name, 'Fast (copy)');
assert.deepEqual(M.moveItem(['a','b','c'], 2, -1), ['a','c','b']);
assert.deepEqual(M.moveItemTo(['a','b','c'], 0, 3), ['b','c','a']);  // to the end
assert.deepEqual(M.moveItemTo(['a','b','c'], 2, 0), ['c','a','b']);
assert.deepEqual(M.moveItemTo(['a','b','c'], 0, 1), ['a','b','c']);  // dropped on itself
{
  const P = (id, on) => ({profile: id, on: on !== false});
  const tiers = [{id:'w', members:[P('a'), P('b'), P('c')]}, {id:'x', members:[P('d'), P('b', false)]}];
  // within a tier: c becomes the second choice, a stays default
  assert.deepEqual(M.moveMember(tiers, {tier:0, index:2}, {tier:0, index:1})[0].members.map(m => m.profile), ['a','c','b']);
  // across tiers: a lands first in x, keeps on/off, leaves w
  const moved = M.moveMember(tiers, {tier:0, index:0}, {tier:1, index:0});
  assert.deepEqual(moved[0].members.map(m => m.profile), ['b','c']);
  assert.deepEqual(moved[1].members.map(m => m.profile), ['a','d','b']);
  assert.deepEqual(tiers[0].members.map(m => m.profile), ['a','b','c']);  // input untouched
  // refused: x already has b; a full tier takes nothing
  assert.equal(M.moveMember(tiers, {tier:0, index:1}, {tier:1, index:0}), null);
  assert.match(M.memberDropProblem(tiers[1], P('b')), /already/);
  const full = {id:'f', members:[1,2,3,4,5,6].map(n => P('p' + n))};
  assert.equal(M.moveMember([tiers[0], full], {tier:0, index:0}, {tier:1, index:6}), null);
  assert.match(M.memberDropProblem(full, P('a')), /full/);
  assert.equal(M.memberDropProblem(tiers[1], P('a')), '');
}
assert.deepEqual(M.profileUsage([{name:'P', tiers:[{members:[{profile:'a'},{agent:'cursor'}]}, {profiles:['b']}]}]), {a:['P'], b:['P']});
assert.deepEqual(M.parseBranches('master, dev/core  x'), ['master','dev/core','x']);
assert.equal(M.esc('<b>'), '&lt;b&gt;');
assert.equal(M.avatarKind(''), 'mascot'); assert.equal(M.avatarKind('cuttle'), 'mascot');
assert.equal(M.avatarKind('🦉'), 'emoji'); assert.equal(M.avatarKind('/output/avatars/1/a.png'), 'image');
assert.ok(M.avatarHtml('/output/avatars/1/a.png').includes('src="/output/avatars/1/a.png"'));
assert.ok(M.avatarHtml('/etc/passwd').includes('cuttle-mascot'));  // unsafe local path → mascot
assert.ok(!M.avatarHtml('<img onerror=x>').includes('<img onerror'));
assert.deepEqual(M.settingItems(['a', {id:'b', label:'B'}, null, {}]), [{modelId:'a',label:'a'},{modelId:'b',label:'B'}]);
assert.equal(M.pinLabel([claude], {agent:'claude', model:'opus', effort:'high'}), 'Claude · opus · high');
assert.equal(M.pinLabel([], {agent:'none'}), 'Any harness');
assert.deepEqual(M.effortLevels(cursor, 'auto'), []);
""" % json.dumps(str(WEB / "js" / "orchestrator" / "orchestrator_model.js"))
    subprocess.run(["node", "-e", script], check=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_js_orchestrate_chip_prefix_carries_playbook():
    script = """
const assert = require('assert');
const C = require(%s);
const chip = {prefix: '/orchestrate ', label: 'orchestrate', category: 'project-cmd'};
assert.ok(C.isChip(chip) && C.isChip({prefix: '/cmd orchestrate @fast '}));
assert.ok(!C.isChip({prefix: '/orchestrator '}) && !C.isChip({prefix: '/cursor '}));
assert.equal(C.playbookOf(chip), '');
const fast = C.withPlaybook(chip, 'fast');
assert.equal(fast.prefix, '/orchestrate @fast '); assert.equal(fast.category, 'project-cmd');
assert.equal(C.playbookOf(fast), 'fast');
assert.equal(C.withPlaybook({prefix: '/cmd orchestrate '}, 'x').prefix, '/cmd orchestrate @x ');
assert.equal(C.withPlaybook(fast, '').prefix, '/orchestrate ');
const pbs = [{id: 'default', name: 'Default org chart'}, {id: 'fast', name: 'Fast - lean'}];
assert.equal(C.labelFor(chip, pbs, 'default'), 'Orchestrate - Default org chart');
assert.equal(C.labelFor(fast, pbs, 'default'), 'Orchestrate - Fast lean');  // stays two segments
assert.equal(C.labelFor(C.withPlaybook(chip, 'gone'), pbs, 'default'), 'Orchestrate - @gone');
assert.equal(C.labelFor(chip, null, ''), 'Orchestrate');
""" % json.dumps(str(WEB / "js" / "chat" / "chat_orchestrate_chip.js"))
    subprocess.run(["node", "-e", script], check=True)
    page = (WEB / "chat_page.html").read_text(encoding="utf-8")
    assert page.index("chat_chip_config.js") < page.index("chat_orchestrate_chip.js") < page.index("/js/chat/chat_page.js")
    orch = (WEB / "orchestrator_page.html").read_text(encoding="utf-8")
    for dep in ("chat_chip_config.js", "agent_pin_picker.js", "chip_config_modal.css", "auth_modal.css"):
        assert dep in orch
    assert orch.index("orchestrator_model.js") < orch.index("agent_pin_picker.js") < orch.index("orchestrator_page.js")


def test_playbook_edit_resyncs_running_supervision(tmp_path, orch, monkeypatch):
    """An edit reaches the supervisor's schedule and waits there for its next
    check-in (no turn of its own); fields set on the schedule itself survive."""
    from api.chat_ops import service as chat_ops, store as ops_store

    db, owner, sup, op, pb = _supervised(tmp_path, monkeypatch, busy=False, every=1200)
    edited = {**pb, "notes": "never touch prod",
              "supervisor": {**pb["supervisor"], "instructions": "Hold it to the plan.",
                             "every_sec": 600, "forbid_branches": ["main"]}}
    service.save_playbook(owner, edited, db=db)
    payload = ops_store.find(op["id"])["payload"]
    assert payload["instructions"] == "Hold it to the plan."
    assert payload["forbidden_branches"] == ["main"]
    assert payload["every"] == 1200  # set on the schedule, not by the playbook
    assert _checkins(sup) == []
    section = "\n".join(chat_ops.changes_section(payload, f"CH-{op['target']:06d}"))
    assert "by playbook Default org chart" in section
    assert "notes" in section and "supervisor settings" in section
    assert "Kept your own check-in interval" in section
    assert len(_notices(db, sup)) == 1

    # A second edit merges into the same record; a no-op save changes nothing.
    service.save_playbook(owner, {**edited, "notes": "never touch prod or staging"}, db=db)
    rev = ops_store.find(op["id"])["payload"]["pending_changes"]["rev"]
    service.save_playbook(owner, {**edited, "notes": "never touch prod or staging"}, db=db)
    assert ops_store.find(op["id"])["payload"]["pending_changes"]["rev"] == rev
    assert _checkins(sup) == [] and len(_notices(db, sup)) == 1


def _supervised(tmp_path, monkeypatch, *, busy=False, **sup_kw):
    from api.chat_ops import service as chat_ops

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(chat_ops, "_target_busy", lambda row: busy)
    sup = db.create_chat_session(owner, "supervisor")
    service.bind(sid, "default", "ship it", db=db)
    pb = service.get_playbook(owner, "default", db=db)
    cfg = model.supervisor_config(pb, service.profile_map(owner, db=db))
    op = chat_ops.supervise(sid, sup, instructions=cfg["instructions"], model_tiers=cfg["model_tiers"],
                            db=db, **sup_kw)["operation"]
    return db, owner, sup, op, pb


def _checkins(sup):
    from api.chat_ops import store as ops_store
    return [o for o in ops_store.listing(sup) if o["kind"] == "checkin" and o["state"] != "cancelled"]


def _notices(db, sup):
    return [m for m in db.get_messages(sup) if m["role"] == "system"]


def test_playbook_edit_that_changes_nothing_on_the_schedule_is_relayed_only_mid_turn(tmp_path, orch, monkeypatch):
    """The target reads the new brief by itself at its next turn: an edit that moves
    none of the schedule's settings reaches the supervisor only when a turn is
    already running, and never shows the user a notice."""
    from api.chat_ops import store as ops_store

    db, owner, sup, op, pb = _supervised(tmp_path, monkeypatch, busy=False)
    service.save_playbook(owner, {**pb, "notes": "new notes"}, db=db)
    assert "pending_changes" not in ops_store.find(op["id"])["payload"]
    monkeypatch.setattr("api.chat_ops.service._target_busy", lambda row: True)
    service.save_playbook(owner, {**pb, "notes": "newer notes"}, db=db)
    pending = ops_store.find(op["id"])["payload"]["pending_changes"]
    assert pending["updated"] == [] and any("notes" in n for n in pending["notes"])
    assert _checkins(sup) == [] and _notices(db, sup) == []


def test_paused_schedule_keeps_playbook_edits_until_it_resumes(tmp_path, orch, monkeypatch):
    """A paused supervisor is never woken: settings still move, the user sees one
    notice per edit, and the record waits for the first check after resume."""
    from api.chat_ops import service as chat_ops, store as ops_store

    db, owner, sup, op, pb = _supervised(tmp_path, monkeypatch, busy=True)
    chat_ops.set_paused(op["id"], owner, True, indefinite=True, db=db)
    service.save_playbook(owner, {**pb, "notes": "org chart moved"}, db=db)
    assert "pending_changes" not in ops_store.find(op["id"])["payload"]  # paused + nothing moved

    first = {**pb, "supervisor": {**pb["supervisor"], "instructions": "Hold it to the plan."}}
    service.save_playbook(owner, first, db=db)
    service.save_playbook(owner, {**first, "supervisor": {**first["supervisor"], "forbid_branches": ["main"]}},
                          db=db)
    after = ops_store.find(op["id"])
    assert after["state"] == "paused" and _checkins(sup) == []
    assert after["payload"]["instructions"] == "Hold it to the plan."
    assert after["payload"]["pending_changes"]["updated"] == ["forbidden_branches", "instructions"]
    assert len(_notices(db, sup)) == 2
    chat_ops.set_paused(op["id"], owner, False, db=db)
    assert _checkins(sup) == []  # resume itself sends nothing; its check does
    assert ops_store.find(op["id"])["payload"]["pending_changes"]["rev"] == 2


def test_playbook_checkpoints_reach_a_schedule_that_never_stored_any(tmp_path, orch, monkeypatch):
    """A schedule with no checkpoints key follows a playbook that had none ({}):
    adding checkpoints to the playbook applies them instead of reading as 'kept'."""
    from api.chat_ops import store as ops_store

    db, owner, sup, op, pb = _supervised(tmp_path, monkeypatch)
    bare = service.save_playbook(owner, {**pb, "supervisor": {**pb["supervisor"], "checkpoints": {}}}, db=db)
    assert "checkpoints" not in ops_store.find(op["id"])["payload"]
    points = {"milestone": {"do": ["wake"]}, "repeat_failures": {"n": 3, "do": ["wake"]}}
    service.save_playbook(owner, {**bare, "supervisor": {**bare["supervisor"], "checkpoints": points}}, db=db)
    payload = ops_store.find(op["id"])["payload"]
    assert set(payload["checkpoints"]) == {"milestone", "repeat_failures"}
    assert "checkpoints" in payload["pending_changes"]["updated"]
    assert "checkpoints" not in payload["pending_changes"]["kept"]


# ── host-owned binding: switch, history, typed command, routes ─────────────


def _second_playbook(owner, db):
    pb = service.get_playbook(owner, "default", db=db)
    return service.save_playbook(owner, {**pb, "id": "strict", "name": "Strict",
                                         "supervisor": {**pb["supervisor"], "instructions": "Be strict.",
                                                        "forbid_branches": ["main"]}}, db=db)


def test_rebind_switches_playbook_keeps_goal_and_resyncs_supervision(tmp_path, orch, monkeypatch):
    from api.chat_ops import service as chat_ops, store as ops_store

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    sup = db.create_chat_session(owner, "supervisor")
    service.bind(sid, "default", "ship it", db=db)
    pb = service.get_playbook(owner, "default", db=db)
    cfg = model.supervisor_config(pb, service.profile_map(owner, db=db))
    op = chat_ops.supervise(sid, sup, instructions=cfg["instructions"], model_tiers=cfg["model_tiers"],
                            db=db)["operation"]
    _second_playbook(owner, db)

    out = service.rebind(sid, owner, playbook_id="strict", db=db)
    assert out["playbook_id"] == "strict" and out["goal"] == "ship it" and out["changed"] == ["playbook"]
    payload = ops_store.find(op["id"])["payload"]
    assert payload["instructions"] == "Be strict." and payload["forbidden_branches"] == ["main"]
    notice = "\n".join(chat_ops.changes_section(payload, f"CH-{sid:06d}"))
    assert "switched" in notice and "`strict`" in notice and "by playbook Strict" in notice
    assert "Strict" in service.brief_block(sid)

    assert service.rebind(sid, owner, goal="ship it", db=db)["changed"] == []  # no-op
    assert service.rebind(sid, owner, goal="ship v2", db=db)["changed"] == ["goal"]
    with pytest.raises(service.OrchestratorError, match="another account"):
        service.rebind(sid, other, goal="x", db=db)
    with pytest.raises(service.OrchestratorError, match="not orchestrating"):
        service.rebind(other_sid, other, goal="x", db=db)
    service.unbind(sid, db=db, source="ui")
    actions = [(e["action"], e["playbook_id"], e["source"]) for e in store.binding_events(session_id=sid)]
    assert actions == [("bind", "default", "cli"), ("switch", "strict", "ui"),
                       ("goal", "strict", "ui"), ("unbind", "strict", "ui")]


def test_orchestrate_command_binds_on_the_host(tmp_path, orch, monkeypatch):
    from api.project_commands import run_host_step

    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    cmd = {"name": "orchestrate", "host_step": "orchestrator.bind"}
    assert "Give the goal" in run_host_step(cmd, "", sid)["reply"]  # unbound, no goal: no agent turn
    assert store.binding(sid) is None
    assert "not found" in run_host_step(cmd, "@ghost do it", sid)["reply"]
    note = run_host_step(cmd, "do the thing", sid)["note"]
    assert "Cuttle bound this chat" in note and "do the thing" in note
    assert store.binding(sid)["goal"] == "do the thing"
    _second_playbook(owner, db)
    run_host_step(cmd, "@strict", sid)  # bound + no goal: switch, keep goal
    row = store.binding(sid)
    assert row["playbook_id"] == "strict" and row["goal"] == "do the thing"
    assert run_host_step({"name": "x", "host_step": "os.system"}, "", sid)["reply"].startswith("Unknown host step")
    assert run_host_step({"name": "x"}, "", sid) is None
    flags.set_enabled("orchestrator", False)
    assert "off" in run_host_step(cmd, "goal", sid)["reply"]
    flags.set_enabled("orchestrator", True)


def test_bundled_orchestrate_command_declares_the_host_step():
    from api.project_commands import _parse_command_file

    path = REPO / ".cuttle_global" / "features" / "orchestrator" / "commands" / "orchestrate.md"
    cmd = _parse_command_file(path, str(REPO))
    assert cmd["host_step"] == "orchestrator.bind" and "api.orchestrator start" not in cmd["body"].split("Do **not**")[0]


def test_binding_routes(tmp_path, monkeypatch, orch):
    ctx = _client(tmp_path, monkeypatch)
    client, sid = ctx["client"], ctx["sid"]
    assert client.get(f"/api/orchestrator/bindings/{sid}").get_json() == {
        "success": True, "session": f"CH-{sid:06d}", "bound": False}
    assert client.put(f"/api/orchestrator/bindings/{sid}", json={"goal": "x"}).status_code == 400
    client.put("/api/agent-profiles/worker", json={"name": "Worker", "agent": "cursor", "model": "auto"})
    client.put("/api/orchestrator/playbooks/mine",
               json={"name": "Mine", "tiers": [{"id": "w", "role": "worker", "profiles": ["worker"]}]})
    service.bind(sid, "mine", "first goal")
    got = client.get(f"/api/orchestrator/bindings/{sid}").get_json()
    assert got["bound"] and got["playbook_id"] == "mine" and got["playbook_name"] == "Mine"
    assert got["goal"] == "first goal"
    put = client.put(f"/api/orchestrator/bindings/{sid}", json={"goal": "second goal"}).get_json()
    assert put["success"] and put["goal"] == "second goal" and put["changed"] == ["goal"]
    assert client.get(f"/api/orchestrator/bindings/{ctx['other_sid']}").status_code == 400


def test_js_orchestration_chip_model():
    script = """
const assert = require('assert');
const M = require(%s);
assert.equal(M.chip({bound: false}), null);
const c = M.chip({bound: true, playbook_id: 'fast', playbook_name: 'Fast', goal: 'ship', bound_at: 5});
assert.equal(c.text, 'Orchestrating · Fast'); assert.equal(c.goal, 'ship'); assert.ok(!c.missing);
assert.ok(M.chip({bound: true, playbook_id: 'gone', playbook_missing: true}).missing);
const pbs = {default_id: 'default', playbooks: [{id: 'default', name: 'Default'},
  {id: 'fast', name: 'Fast'}, {id: 'bad', name: 'Bad', missing_profiles: ['ghost']}]};
const fresh = M.dialogDefaults({bound: false}, pbs);
assert.equal(fresh.editing, false); assert.equal(fresh.playbookId, 'default');
assert.equal(fresh.playbooks[0].label, 'Default (default)');
assert.deepEqual(M.submitPlan(fresh, {playbookId: 'fast', goal: ' ship it '}),
  {ok: true, kind: 'send', text: '/orchestrate @fast ship it'});
assert.deepEqual(M.submitPlan(fresh, {playbookId: 'fast', goal: ''}).errors, ['Give the orchestrator a goal.']);
assert.ok(M.submitPlan(fresh, {playbookId: 'bad', goal: 'x'}).errors[0].includes('missing profiles: ghost'));
const edit = M.dialogDefaults({bound: true, playbook_id: 'fast', goal: 'ship'}, pbs);
assert.equal(edit.editing, true); assert.equal(edit.playbookId, 'fast');
assert.deepEqual(M.submitPlan(edit, {playbookId: 'fast', goal: 'ship'}), {ok: true, kind: 'none', body: {}});
assert.deepEqual(M.submitPlan(edit, {playbookId: 'default', goal: 'ship v2'}),
  {ok: true, kind: 'put', body: {playbook_id: 'default', goal: 'ship v2'}});
""" % json.dumps(str(WEB / "js" / "chat" / "chat_orchestration_model.js"))
    subprocess.run(["node", "-e", script], check=True)
    page = (WEB / "chat_page.html").read_text(encoding="utf-8")
    assert 'id="chatOrchestrationSlot"' in page
    assert (page.index("/js/chat/chat_orchestration_model.js") < page.index("/js/chat/chat_orchestration.js")
            < page.index("/js/chat/chat_page.js"))


def test_playbook_checkpoints_default_validate_and_reach_the_supervise_command(tmp_path, orch, monkeypatch):
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    pb = service.get_playbook(owner, "default", db=db)
    assert pb["supervisor"]["checkpoints"] == {"milestone": {"do": ["wake"]}, "repeat_failures": {"n": 3, "do": ["wake"]}}
    assert model.normalize_playbook({**pb, "supervisor": {**pb["supervisor"], "checkpoints": {}}})["supervisor"]["checkpoints"] == {}
    with pytest.raises(model.PlaybookError, match="checkpoints"):
        model.normalize_playbook({**pb, "supervisor": {**pb["supervisor"], "checkpoints": ["tests"]}})
    with pytest.raises(service.OrchestratorError, match="unknown checkpoint"):
        service.save_playbook(owner, {**pb, "supervisor": {**pb["supervisor"], "checkpoints": {"vibes": {}}}}, db=db)
    saved = service.save_playbook(owner, {**pb, "supervisor": {**pb["supervisor"], "checkpoints": {
        "tests": {"when": "fail", "do": ["supervisor:expert-claude"]}}}}, db=db)
    assert saved["supervisor"]["checkpoints"] == {"tests": {"when": "fail", "do": ["supervisor:expert-claude"]}}
    argv = model.supervisor_argv(saved, "CH-000009", service.profile_map(owner, db=db))
    assert json.loads(argv[argv.index("--checkpoints") + 1]) == saved["supervisor"]["checkpoints"]


def test_playbook_checkpoint_edit_reaches_running_schedule_and_dialog_defaults(tmp_path, orch, monkeypatch):
    from api.chat_ops import service as chat_ops, store as ops_store

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    sup = db.create_chat_session(owner, "supervisor")
    service.bind(sid, "default", "ship it", db=db)
    pb = service.get_playbook(owner, "default", db=db)
    cfg = model.supervisor_config(pb, service.profile_map(owner, db=db))
    op = chat_ops.supervise(sid, sup, instructions=cfg["instructions"], model_tiers=cfg["model_tiers"],
                            checkpoints=cfg["checkpoints"], db=db)["operation"]
    ops_store.patch_result(op["id"], paths_base="abc", failure_counts={"x": 2})
    defaults = chat_ops.supervise_defaults(sid, owner, db=db)["playbook"]
    assert defaults["id"] == "default" and defaults["checkpoints"] == cfg["checkpoints"]
    assert chat_ops.supervise_defaults(other_sid, other, db=db)["playbook"] is None

    service.save_playbook(owner, {**pb, "supervisor": {**pb["supervisor"], "checkpoints": {"tests": {}}}}, db=db)
    after = ops_store.find(op["id"])
    assert after["payload"]["checkpoints"] == {"tests": {"when": "pass", "do": ["wake"]}}
    assert "paths_base" not in after["result"] and "failure_counts" not in after["result"]
    notice = "\n".join(chat_ops.changes_section(after["payload"], f"CH-{sid:06d}"))
    assert "wake checkpoints" in notice


def test_binding_lists_the_orchestrators_agents_running_first(tmp_path, orch, monkeypatch):
    """The header chip's popover shows the chat's sub-agents: one row per child
    chat, running ones first, newest batches before older ones."""
    from types import SimpleNamespace as NS

    db, owner, other, sid, other_sid = _seed_db(tmp_path)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    service.bind(sid, "default", "ship it", db=db)

    def child(session, name, status, idx=0):
        return NS(session_id=session, display_name=name, label=name, avatar="", agent="codex", model="m",
                  effort="low", status=status, profile_id="", finished_at="", sort_index=idx)

    newest = NS(children=[child(61, "Judge", "done", 0), child(62, "Scout", "running", 1)])
    older = NS(children=[child(61, "Judge", "failed"), child(60, "Builder", "done")])
    monkeypatch.setattr("api.subagents.store.list_batches_for_parent", lambda _db, parent: [newest, older])
    agents = service.get_binding(sid, db=db)["agents"]
    assert [(a["handle"], a["status"]) for a in agents] == [
        ("CH-000062", "running"), ("CH-000061", "done"), ("CH-000060", "done")]
    assert agents[0]["name"] == "Scout" and agents[0]["agent"] == "codex"
