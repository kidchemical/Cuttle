"""Live reports coalesce at both HTTP/provider and multi-pane browser layers."""
import json
import subprocess
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from api import agent_usage, usage_live


@pytest.fixture(autouse=True)
def clear_cache():
    usage_live._cache.clear()
    usage_live._locks.clear()


def test_concurrent_reports_share_fetch_and_expire(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(usage_live.time, "monotonic", lambda: now[0])
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return "<cuttle_meters>[]</cuttle_meters>"

    monkeypatch.setattr(agent_usage, "run_codex_usage", fetch)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(usage_live.usage_snapshot, "codex") for _ in range(3)]
        assert entered.wait(5)
        release.set()
        reports = [future.result() for future in futures]
    assert len(calls) == 1
    assert reports[0] == reports[1] == reports[2]
    now[0] += 60
    usage_live.usage_snapshot("codex")
    assert len(calls) == 2


def test_failure_keeps_snapshot_and_is_cached(monkeypatch):
    monkeypatch.setattr(agent_usage, "run_codex_usage", lambda: "previous report")
    old = usage_live.usage_snapshot("codex")
    usage_live._cache[("codex", 30)] = (-1000, old)

    def fail():
        raise RuntimeError("secret credential")

    monkeypatch.setattr(agent_usage, "run_codex_usage", fail)
    result = usage_live.usage_snapshot("codex")
    assert result["markdown"] == "previous report"
    assert result["updated_at"] == old["updated_at"]
    assert "secret" not in json.dumps(result)
    assert usage_live.usage_snapshot("codex") == result


@pytest.mark.parametrize("agent", ["codex", "muse", "hermes", "opencode", "claude"])
def test_live_command_and_static_command_stay_separate(monkeypatch, agent):
    calls = []
    monkeypatch.setattr(agent_usage, "run_" + agent + "_usage", lambda **kwargs: calls.append(kwargs) or "static meters")
    result = agent_usage.handle_agent_usage_slash(agent, "/usage-live 7d")
    assert result.startswith("<cuttle_usage_live>")
    assert agent_usage.handle_agent_usage_slash(agent, "/usage") == "static meters"
    assert len(calls) == 2


def test_cursor_live_parser_and_reply(monkeypatch):
    from api import cursor_agent_commands as cursor
    assert cursor.parse_cursor_agent_slash("/usage-live") == ("usage-live", "")
    monkeypatch.setattr(cursor, "_run_cursor_usage", lambda: "cursor report")
    assert "cursor report" in usage_live.live_usage_reply("cursor")


def test_http_owner_gate_validation_and_cache(monkeypatch):
    from flask import Flask
    from api import http_authz
    app = Flask(__name__)
    app.register_blueprint(usage_live.usage_live_bp)
    client = app.test_client()
    monkeypatch.setattr(http_authz, "current_user", lambda: None)
    assert client.get("/api/usage-live?agent=codex").status_code == 401
    monkeypatch.setattr(http_authz, "current_user", lambda: {"auth_provider": "guest"})
    assert client.get("/api/usage-live?agent=codex").status_code == 403
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    from api.auth_db import get_auth_db
    db = get_auth_db()
    owner = db.get_user_by_id(db.create_user("owner@local", "Owner", "local", password="x"))
    monkeypatch.setattr(http_authz, "current_user", lambda: owner)
    calls = []
    monkeypatch.setattr(agent_usage, "run_codex_usage", lambda: calls.append(1) or "meters")
    for _ in range(3):
        response = client.get("/api/usage-live?agent=codex")
        assert response.status_code == 200
        assert response.json["markdown"] == "meters"
        assert response.headers["Cache-Control"] == "no-store"
    assert len(calls) == 1
    assert client.get("/api/usage-live?agent=unknown").status_code == 400
    assert client.get("/api/usage-live?agent=muse&days=garbage").status_code == 400


def _snapshot_ctx(tmp_path, monkeypatch):
    from flask import Flask
    from api import auth_db as auth_db_mod
    from api import http_authz
    db_path = tmp_path / "usage-live-snap.db"
    monkeypatch.setattr(auth_db_mod, "DB_PATH", db_path)
    auth_db_mod._db_instance = None
    db = auth_db_mod.AuthDatabase(db_path)
    auth_db_mod._db_instance = db
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    other = db.create_user("other@local", "Other", "local", password="x")
    sid = db.create_chat_session(owner, "usage")
    monkeypatch.setattr(
        http_authz, "current_user",
        lambda: {"id": owner, "auth_provider": "local"},
    )
    app = Flask(__name__)
    app.register_blueprint(usage_live.usage_live_bp)
    return app.test_client(), db, sid, owner, other


def _store_live_report(db, sid, monkeypatch, agent="codex", prefix="budgets:\n\n", suffix="\n\ndone"):
    calls = []

    def fake():
        calls.append(1)
        return "report v%d" % len(calls)

    monkeypatch.setattr(agent_usage, "run_" + agent + "_usage", fake)
    wrapper = usage_live.live_usage_reply(agent)
    mid = db.add_message(sid, "assistant", prefix + wrapper + suffix)
    return mid, calls


def test_snapshot_persist_rewrites_wrapper_only(tmp_path, monkeypatch):
    client, db, sid, _owner, _other = _snapshot_ctx(tmp_path, monkeypatch)
    mid, _calls = _store_live_report(db, sid, monkeypatch)
    usage_live._cache.clear()
    response = client.post(
        "/api/usage-live/snapshot", json={"message_id": mid, "agent": "codex"})
    assert response.status_code == 200
    body = response.json
    assert body["ok"] is True and body["persisted"] is True
    assert body["message_id"] == mid
    row = db.get_message_by_id(mid)
    assert row["content"].startswith("budgets:\n\n<cuttle_usage_live>")
    assert row["content"].endswith("</cuttle_usage_live>\n\ndone")
    assert "report v2" in row["content"]
    assert "report v1" not in row["content"]


def test_snapshot_persist_skips_identical_content(tmp_path, monkeypatch):
    client, db, sid, _owner, _other = _snapshot_ctx(tmp_path, monkeypatch)
    mid, _calls = _store_live_report(db, sid, monkeypatch)
    usage_live._cache.clear()
    first = client.post(
        "/api/usage-live/snapshot", json={"message_id": mid, "agent": "codex"})
    assert first.json["persisted"] is True
    # Server cache expired but the provider reports the same text: no rewrite.
    seen = []
    monkeypatch.setattr(
        agent_usage, "run_codex_usage", lambda: seen.append(1) or "report v2")
    usage_live._cache.clear()
    again = client.post(
        "/api/usage-live/snapshot", json={"message_id": mid, "agent": "codex"})
    assert again.status_code == 200
    assert seen == [1]
    assert again.json["persisted"] is False


def test_snapshot_persist_rejects_bad_targets(tmp_path, monkeypatch):
    client, db, sid, _owner, other = _snapshot_ctx(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        agent_usage, "run_codex_usage", lambda: calls.append(1) or "meters")
    mid, _ = _store_live_report(db, sid, monkeypatch)
    plain = db.add_message(sid, "assistant", "just text")
    user_row = db.add_message(sid, "user", "hi")

    assert client.post("/api/usage-live/snapshot",
                       data="not json",
                       content_type="text/plain").status_code == 415
    assert client.post("/api/usage-live/snapshot",
                       json={"message_id": mid, "agent": "nope"}).status_code == 400
    assert client.post("/api/usage-live/snapshot",
                       json={"message_id": 999999, "agent": "codex"}).status_code == 404
    assert client.post("/api/usage-live/snapshot",
                       json={"message_id": plain, "agent": "codex"}).status_code == 422
    assert client.post("/api/usage-live/snapshot",
                       json={"message_id": user_row, "agent": "codex"}).status_code == 404
    assert client.post("/api/usage-live/snapshot",
                       json={"message_id": mid, "agent": "muse"}).status_code == 422
    # Validation happens before any provider/CLI call.
    assert calls == []

    from api import http_authz
    monkeypatch.setattr(
        http_authz, "current_user",
        lambda: {"id": other, "auth_provider": "local"},
    )
    assert client.post("/api/usage-live/snapshot",
                       json={"message_id": mid, "agent": "codex"}).status_code == 404


def test_snapshot_persist_transient_error_not_stored(tmp_path, monkeypatch):
    client, db, sid, _owner, _other = _snapshot_ctx(tmp_path, monkeypatch)
    mid, _calls = _store_live_report(db, sid, monkeypatch)
    before = db.get_message_by_id(mid)["content"]

    def fail():
        raise RuntimeError("provider down")

    monkeypatch.setattr(agent_usage, "run_codex_usage", fail)
    usage_live._cache.clear()
    response = client.post(
        "/api/usage-live/snapshot", json={"message_id": mid, "agent": "codex"})
    assert response.status_code == 200
    assert response.json["persisted"] is False
    assert db.get_message_by_id(mid)["content"] == before


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_browser_broker_coalesces_and_stops_when_removed():
    script = Path(__file__).parents[2] / "web/js/chat/chat_usage_live.js"
    code = r'''
const assert = require('assert');
const U = require(process.argv[1]);
let now = 100000, requests = 0, timer, painted = [0, 0, 0];
Date.now = () => now;
let complete;
const host = {
  fetch: () => { requests++; return new Promise(resolve => { complete = resolve; }); },
  setTimeout: (fn, delay) => { timer = {fn, delay}; return 1; },
  clearTimeout: () => { timer = null; }
};
const broker = U.createBroker(host);
const sources = painted.map((_, i) => () => [{key:'codex:30',
  apply: () => { painted[i]++; }, failed: () => {} }]);
sources.forEach(s => broker.add(s));
assert.equal(requests, 1);
complete({ok:true, json: async () => ({markdown:'report'})});
setImmediate(() => {
  broker.tick();
  assert(painted.every(n => n > 0));
  now += 30000;
  broker.tick();
  assert.equal(timer.delay, 30000); // DOM activity cannot postpone refresh
  now += 30000;
  timer.fn();
  assert.equal(requests, 2);
  sources.forEach(s => broker.remove(s));
  assert.equal(timer, null);
  const html = U.render('<cuttle_usage_live>'+JSON.stringify({agent:'codex', days:30, markdown:'meters'})+'</cuttle_usage_live>', s=>s);
  assert(html.includes('data-usage-agent="codex"'));
  assert.equal(U.render('ordinary snapshot', s=>s), null);
});
'''
    subprocess.run(["node", "-e", code, str(script)], check=True, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_all_usage_reports_share_layout_without_losing_provider_details():
    from tests.harness.test_agent_usage_slash import (
        CURSOR_USAGE_FAKE, CODEX_USAGE_FAKE, MUSE_USAGE_FAKE,
        HERMES_INSIGHTS_SAMPLE, OPENCODE_STATS_SAMPLE,
    )
    from api.cursor_agent_commands import format_cursor_usage_markdown
    reports = {
        "cursor": format_cursor_usage_markdown(CURSOR_USAGE_FAKE),
        "codex": agent_usage.format_codex_usage_markdown(CODEX_USAGE_FAKE),
        "muse": agent_usage.format_muse_usage_markdown(MUSE_USAGE_FAKE),
        "hermes": agent_usage.format_hermes_usage_markdown({
            **agent_usage.parse_hermes_insights_text(HERMES_INSIGHTS_SAMPLE), "success": True}),
        "opencode": agent_usage.format_opencode_usage_markdown({
            **agent_usage.parse_opencode_stats_text(OPENCODE_STATS_SAMPLE), "success": True}),
    }
    script = Path(__file__).parents[2] / "web/js/chat/chat_usage_live.js"
    code = r'''
const assert = require('assert');
const U = require(process.argv[1]);
const reports = JSON.parse(process.argv[2]);
const format = text => U.render(text, format) ?? text;
for (const [agent, text] of Object.entries(reports)) {
  const staticHtml = format(text);
  assert(staticHtml.includes('class="usage-report-heading"'), agent);
  assert(staticHtml.includes('class="usage-report-details"'), agent);
  assert(staticHtml.includes('<cuttle_meters>'), agent);
  const liveHtml = format('<cuttle_usage_live>'+JSON.stringify({agent, markdown:text})+'</cuttle_usage_live>');
  assert(liveHtml.includes(staticHtml), agent);
}
assert(format(reports.hermes).includes('Local Hermes insights'));
assert(format(reports.opencode).includes('Local OpenCode stats'));
assert(format(reports.muse).includes('Meta does not expose Muse'));
assert(format(reports.cursor).includes('<dt>Plan</dt>'));
assert(!format(reports.opencode).includes('last **30** days'));
assert(format(reports.opencode).includes('last <strong>30</strong> days'));
assert.equal(U.render('ordinary message', format), null);
assert.equal(U.render('❌ **Cursor usage**\n\nUnavailable', format), null);
const untrusted = format('**Cursor — usage**\n\n- Plan: <img src=x onerror=evil>\n\nBody');
assert(untrusted.includes('&lt;img'));
assert(!untrusted.includes('<img'));
'''
    subprocess.run(["node", "-e", code, str(script), json.dumps(reports)],
                   check=True, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_browser_wake_rescans_after_hidden_tab():
    script = Path(__file__).parents[2] / "web/js/chat/chat_usage_live.js"
    code = r'''
const assert = require('assert');
const realSetTimeout = setTimeout;
global.MutationObserver = class { constructor(cb) {} observe() {} disconnect() {} };
global.addEventListener = () => {};
global.requestAnimationFrame = (fn) => { fn(); return 1; };
global.setTimeout = () => 0;
global.clearTimeout = () => {};
global.setInterval = () => 0;
global.clearInterval = () => {};
const U = require(process.argv[1]);
let requests = 0;
const report = {};
const status = {};
const node = {
  isConnected: true,
  dataset: {usageAgent: 'codex', usageDays: '30'},
  getBoundingClientRect: () => ({width: 10, height: 10, bottom: 10, top: 0, right: 10, left: 0}),
  querySelector: (sel) => sel === '.usage-live-report' ? report
    : (sel === '.usage-live-status' ? status : null),
  closest: () => null,
};
let visible = false; // space tab hidden: no usage node in the layout
global.innerHeight = 100;
global.innerWidth = 100;
global.document = {
  hidden: false,
  body: {},
  addEventListener: () => {},
  querySelectorAll: (sel) => (sel === '.cuttle-usage-live' && visible) ? [node] : [],
};
global.fetch = (url) => {
  requests++;
  assert(url.startsWith('/api/usage-live?agent=codex'));
  return Promise.resolve({ok: true,
    json: async () => ({agent: 'codex', days: 30, markdown: 'meters', updated_at: 1})});
};
const wait = () => new Promise((resolve) => realSetTimeout(resolve, 20));
U.start((text) => text);
(async () => {
  await wait();
  assert.equal(requests, 0); // nothing visible: nothing fetched
  visible = true; // space tab activated again (no DOM mutation inside the frame)
  U.wake(); // shell wake-up re-triggers the scan path
  await wait();
  assert.equal(requests, 1);
  assert.equal(report.innerHTML, 'meters');
})().catch((error) => { console.error(error); process.exit(1); });
'''
    subprocess.run(["node", "-e", code, str(script)],
                   check=True, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_browser_persist_writes_once_per_snapshot():
    script = Path(__file__).parents[2] / "web/js/chat/chat_usage_live.js"
    code = r'''
const assert = require('assert');
const U = require(process.argv[1]);
const posts = [];
let failNext = 0;
const host = {
  fetch: (url, opts) => {
    posts.push({url, body: opts && opts.body});
    const status = failNext ? 404 : 200;
    failNext = 0;
    return Promise.resolve({ok: status === 200, status, json: async () => ({})});
  },
};
const bubbleFor = (id) => ({dataset: {messageId: String(id)}});
const nodeFor = (id) => ({
  isConnected: true,
  closest: () => (id == null ? null : bubbleFor(id)),
});
const targets = [{node: nodeFor(7)}, {node: nodeFor(7)}, {node: nodeFor(null)}];
const data = (updatedAt) => ({agent: 'codex', days: 30, markdown: 'm', updated_at: updatedAt});
(async () => {
  await U.persistSnapshot(host, 'codex:30', targets, data(100));
  assert.equal(posts.length, 1); // one POST per snapshot, not per pane/node
  assert.equal(JSON.parse(posts[0].body).message_id, 7);
  assert(posts[0].url === '/api/usage-live/snapshot');
  await U.persistSnapshot(host, 'codex:30', targets, data(100));
  assert.equal(posts.length, 1); // same snapshot: no repeat write
  await U.persistSnapshot(host, 'codex:30', targets, data(200));
  assert.equal(posts.length, 2); // newer snapshot: write again
  await U.persistSnapshot(host, 'codex:30', targets, {agent: 'codex', error: 'x'});
  assert.equal(posts.length, 2); // transient failure: never stored
  failNext = 1;
  await U.persistSnapshot(host, 'codex:30', [{node: nodeFor(9)}], data(300));
  assert.equal(posts.length, 3);
  await U.persistSnapshot(host, 'codex:30', [{node: nodeFor(9)}], data(300));
  assert.equal(posts.length, 3); // deleted row (404): stop retrying
})().catch((error) => { console.error(error); process.exit(1); });
'''
    subprocess.run(["node", "-e", code, str(script)],
                   check=True, capture_output=True, text=True, encoding="utf-8")
