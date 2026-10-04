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
    monkeypatch.setattr(http_authz, "current_user", lambda: {"auth_provider": "local"})
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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_browser_broker_coalesces_and_stops_when_removed():
    script = Path(__file__).parents[1] / "web/js/chat_usage_live.js"
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
    subprocess.run(["node", "-e", code, str(script)], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_all_usage_reports_share_layout_without_losing_provider_details():
    from tests.test_agent_usage_slash import (
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
    script = Path(__file__).parents[1] / "web/js/chat_usage_live.js"
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
                   check=True, capture_output=True, text=True)
