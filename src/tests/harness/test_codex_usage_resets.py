"""Saved resets never consume on read; explicit redemption is owner-only."""
import json
import sys
import uuid
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask
from api import agent_usage, http_authz, usage_live
from scripts.utilities import codex_account


def test_account_rpc_handshake_and_specific_credit(tmp_path, monkeypatch):
    fake = tmp_path / "codex"
    fake.write_text(f"#!{sys.executable}\n" + '''
import sys, json
initialized = False
for line in sys.stdin:
    msg = json.loads(line)
    method = msg['method']
    if method == 'initialized':
        initialized = True
        continue
    if method == 'initialize':
        result = {}
    elif method == 'account/rateLimits/read':
        assert initialized
        result = {'accountId':'account', 'rateLimitResetCredits':{'availableCount':2}}
    elif method == 'account/rateLimitResetCredit/consume':
        assert initialized
        assert msg['params']['creditId'] == 'credit-1'
        assert msg['params']['idempotencyKey'] == 'attempt'
        result = {'outcome':'reset'}
    else:
        raise AssertionError(method)
    print(json.dumps({'id':msg['id'], 'result':result}), flush=True)
''')
    fake.chmod(0o755)
    monkeypatch.setattr(codex_account, "codex_executable", lambda: str(fake))
    assert codex_account.read_codex_account_limits()["rateLimitResetCredits"]["availableCount"] == 2
    assert codex_account.consume_codex_reset("credit-1", "attempt", "account") == {"outcome": "reset"}
    with pytest.raises(ValueError, match="account changed"):
        codex_account.consume_codex_reset("credit-1", "attempt", "other")


def test_supported_usage_includes_reset_details(monkeypatch):
    credits = {"availableCount": 2, "credits": [{"id": "c", "expiresAt": 1791153536}]}
    monkeypatch.setattr(codex_account, "read_codex_account_limits", lambda: {
        "accountId": "a", "rateLimits": {"planType": "pro", "primary": {"usedPercent": 40}},
        "rateLimitResetCredits": credits})
    data = agent_usage.fetch_codex_account_usage()
    assert data["account_id"] == "a"
    assert data["rate_limit"]["primary_window"]["used_percent"] == 40
    md = agent_usage.format_codex_usage_markdown(data)
    assert "Rate-limit resets available: **2**" in md
    payload = json.loads(md.split("<cuttle_codex_resets>")[1].split("</cuttle_codex_resets>")[0])
    assert payload["credits"] == credits["credits"]
    assert payload["account_id"] == "a"


def test_unknown_details_do_not_invent_expirations():
    md = agent_usage.format_codex_usage_markdown({"success": True,
        "rate_limit_reset_credits": {"available_count": 2, "credits": None}})
    assert "expiration dates unavailable" in md
    assert "<cuttle_codex_resets>" not in md


def test_codex_reset_caption_is_meter_tooltip():
    data = {"success": True, "rate_limit": {
        "primary_window": {"used_percent": 20, "reset_at": 1790926320},
        "secondary_window": {"used_percent": 3, "reset_after_seconds": 86400}}}
    md = agent_usage.format_codex_usage_markdown(data)
    meters = json.loads(md.split("<cuttle_meters>")[1].split("</cuttle_meters>")[0])["rows"]
    assert [row["label"] for row in meters] == ["5-hour", "Weekly", "Credits"]
    assert meters[0]["tooltip"] == "Resets"
    assert meters[0]["tooltip_at"] == 1790926320
    assert meters[1]["tooltip"] == "Resets"
    now = int(agent_usage.datetime.now(agent_usage.timezone.utc).timestamp())
    assert abs(meters[1]["tooltip_at"] - now - 86400) <= 2
    assert "tooltip" not in meters[2]


@pytest.fixture
def client(monkeypatch):
    usage_live._cache.clear()
    usage_live._locks.clear()
    monkeypatch.delenv("OWNER_USER_EMAIL", raising=False)
    monkeypatch.setattr(http_authz, "current_user", lambda: _owner())
    app = Flask(__name__)
    app.register_blueprint(usage_live.usage_live_bp)
    return app.test_client()


def _owner():
    """The install's first account — the single-user owner."""
    from api.auth_db import get_auth_db

    db = get_auth_db()
    uid = db.first_account_id() or db.create_user("owner@local", "Owner", "local", password="x")
    return db.get_user_by_id(uid)


def body():
    return {"credit_id": "credit-1", "account_id": "a", "confirmed": True,
            "idempotency_key": str(uuid.uuid4())}


def test_redemption_owner_and_confirmation_gate(client, monkeypatch):
    def unexpected(*args):
        pytest.fail("No redemption should be attempted")
    monkeypatch.setattr(codex_account, "consume_codex_reset", unexpected)
    for user, code in [(None, 401), ({"auth_provider": "guest"}, 403)]:
        monkeypatch.setattr(http_authz, "current_user", lambda: user)
        assert client.post("/api/usage/codex/reset", json=body()).status_code == code
    monkeypatch.setattr(http_authz, "current_user", lambda: _owner())
    for change in [{"confirmed": False}, {"credit_id": ""}, {"idempotency_key": "bad"}]:
        assert client.post("/api/usage/codex/reset", json={**body(), **change}).status_code == 400
    assert client.get("/api/usage/codex/reset").status_code == 405


@pytest.mark.parametrize("outcome", ["reset", "alreadyRedeemed", "nothingToReset", "noCredit"])
def test_redemption_refreshes_and_preserves_retry_key(client, monkeypatch, outcome):
    attempts = []
    monkeypatch.setattr(codex_account, "consume_codex_reset",
        lambda *args: attempts.append(args) or {"outcome": outcome})
    monkeypatch.setattr(agent_usage, "run_codex_usage", lambda: "fresh")
    usage_live._cache[("codex", 30)] = (usage_live.time.monotonic(), {"markdown": "old"})
    params = body()
    for _ in range(2):
        response = client.post("/api/usage/codex/reset", json=params)
        assert response.status_code == 200
        assert response.json["snapshot"]["markdown"] == "fresh"
        assert response.json["outcome"] == outcome
    assert attempts == [("credit-1", params["idempotency_key"], "a")] * 2


@pytest.mark.parametrize("exc,code", [(RuntimeError("secret"), 502), (ValueError("secret"), 409)])
def test_redemption_errors_hide_provider_secrets(client, monkeypatch, exc, code):
    def fail(*args):
        raise exc
    monkeypatch.setattr(codex_account, "consume_codex_reset", fail)
    response = client.post("/api/usage/codex/reset", json=body())
    assert response.status_code == code
    assert "secret" not in response.get_data(as_text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_reset_browser_render_confirmation_retry_and_refresh():
    script = Path(__file__).parents[2] / "web/js/chat/chat_usage_live.js"
    code = r'''
const assert = require('assert');
const U = require(process.argv[1]);
const now = Math.floor(Date.now()/1000);
const credits = [
 {id:'later', title:'<img onerror=evil>', status:'available', resetType:'codexRateLimits', expiresAt:now+5000},
 {id:'earlier', title:'Full reset', status:'available', resetType:'codexRateLimits', expiresAt:now+1000},
 {id:'expired', status:'available', resetType:'codexRateLimits', expiresAt:now-1},
 {id:'never', status:'available', resetType:'codexRateLimits', expiresAt:null}
];
const text = 'meters\n<cuttle_codex_resets>'+JSON.stringify({available_count:5, credits, account_id:'a'})+'</cuttle_codex_resets>';
const format = s => U.render(s, format) ?? s;
const html = format(text);
assert(html.includes('&lt;img onerror=evil&gt;'));
assert(!html.includes('<img onerror=evil>'));
assert(html.indexOf('data-codex-reset="earlier"') < html.indexOf('data-codex-reset="later"'));
assert(!html.includes('data-codex-reset="expired"'));
assert(html.includes('No expiration'));
assert(html.includes('only part of the list'));
assert(format('<cuttle_usage_live>'+JSON.stringify({agent:'codex', markdown:text})+'</cuttle_usage_live>').includes('Use reset'));

// A pre-redemption poll must not overwrite the refreshed report.
let finish, painted = [];
const broker = U.createBroker({fetch:()=>new Promise(r=>{finish=r;}), setTimeout:()=>1, clearTimeout:()=>{}});
broker.add(()=>[{key:'codex:30', apply:d=>painted.push(d.markdown), failed:()=>{}}]);
broker.update('codex:30', {markdown:'new'});
finish({ok:true, json:async()=>({markdown:'old'})});

const handlers = {}, storage = new Map(), sent = [];
global.location = {origin:'http://test'};
global.document = {
 hidden:false, body:{}, querySelectorAll:()=>[],
 addEventListener:(name, fn)=>{handlers[name]=fn;}
};
global.MutationObserver = class {observe(){} disconnect(){}};
global.addEventListener = ()=>{};
global.requestAnimationFrame = ()=>{};
global.sessionStorage = {getItem:k=>storage.get(k), setItem:(k,v)=>storage.set(k,v)};
let status = {textContent:''}, report;
const button = {disabled:false, dataset:{codexReset:'earlier', accountId:'a'},
 closest:selector=>selector==='.codex-usage-report'?report:{querySelector:()=>({textContent:'Full reset'})}};
report = {dataset:{}, innerHTML:'', querySelector:()=>status, querySelectorAll:()=>[button]};
let confirmed = false;
global.confirm = ()=>confirmed;
global.fetch = async (url, options) => {
 sent.push(JSON.parse(options.body));
 if (sent.length===1) throw new Error('Network failure');
 return {ok:true, json:async()=>({message:'Reset redeemed.', snapshot:{markdown:'fresh'}})};
};
U.start(format);
const event = {target:{closest:()=>button}};
(async()=>{
 await handlers.click(event);
 assert.equal(sent.length, 0); // cancel spends nothing
 confirmed = true;
 await handlers.click(event);
 assert.equal(button.disabled, false);
 assert(status.textContent.includes('Network failure'));
 await handlers.click(event);
 assert.equal(sent.length, 2);
 assert.equal(sent[0].credit_id, 'earlier');
 assert.equal(sent[0].idempotency_key, sent[1].idempotency_key);
 assert.equal(sent[0].confirmed, true);
 assert(report.innerHTML.includes('fresh'));
 assert(report.innerHTML.includes('Reset redeemed.'));
 await new Promise(r=>setImmediate(r));
 assert(!painted.includes('old'));
})().catch(e=>{console.error(e); process.exitCode=1;});
'''
    subprocess.run(["node", "-e", code, str(script)], check=True, capture_output=True, text=True)
