"""Billing order client: composer popup owner + Settings → Agents → Billing helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
WEB = REPO / "src" / "web"

HARNESS = r"""
require(process.env.BILLING);
require(process.env.SETTINGS);
const B = globalThis.CuttleChatAgentBilling;
const S = globalThis.CuttleSettingsAgentBilling;
const posts = [];
let sid = '';
const ctl = B.create({
  getSessionId: () => sid,
  fetch: (url, opts) => {
    if (opts && opts.method === 'POST') posts.push({url, body: JSON.parse(opts.body)});
    const body = opts && opts.method === 'POST'
      ? {success: true, billing: JSON.parse(opts.body).billing, api_ready: 'ready'}
      : {success: true, billing: 'default', default: 'api'};
    return Promise.resolve({json: () => Promise.resolve(body)});
  },
});
(async () => {
  const out = {};
  out.noSpec = ctl.spec('codex');
  const spec = ctl.spec('claude');
  await spec.load();
  out.options = spec.options().map((o) => o.label);
  // Unsent chat: the pick waits for the first send, then rides agent_pins.
  spec.set('api');
  out.pendingCurrent = spec.current();
  const body = {message: '/claude hi', agent_pins: {claude: {model: 'opus'}}};
  ctl.attachToRequest(body);
  out.body = body;
  const again = {message: 'x'};
  ctl.attachToRequest(again);
  out.second = again;
  // Chat with an id: POST the pin.
  sid = '42';
  spec.set('subscription');
  await new Promise((r) => setTimeout(r, 0));
  out.posts = posts;
  out.current = spec.current();
  out.patch = S.patchFrom({prefer: 'api', fallback: 1, budget: ' 75 '});
  out.patchEmpty = S.patchFrom({prefer: 'x', budget: ''});
  out.noKey = S.statusLine({has_key: false});
  out.spent = S.statusLine({has_key: true, month_usd: 1.5, monthly_budget_usd: 200});
  console.log(JSON.stringify(out));
})();
"""


@pytest.fixture(scope="module")
def result():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    # Keep the inherited environment: Windows node aborts without SystemRoot.
    env = dict(os.environ, BILLING=str(WEB / "js/chat/chat_agent_billing.js"),
               SETTINGS=str(WEB / "js/settings/settings_agent_billing.js"))
    proc = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, encoding="utf-8",
                          env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_popup_spec_only_for_agents_that_bill_both_ways(result):
    assert result["noSpec"] is None
    assert result["options"] == ["Default (API first)", "Subscription first", "API first"]


def test_pre_session_pick_rides_the_first_send_once(result):
    assert result["pendingCurrent"] == "api"
    assert result["body"]["agent_pins"]["claude"] == {"model": "opus", "billing": "api"}
    assert "agent_pins" not in result["second"]


def test_pick_on_an_existing_chat_posts_the_pin(result):
    assert result["posts"] == [{"url": "/api/claude/billing", "body": {"session": "42", "billing": "subscription"}}]
    assert result["current"] == "subscription"


def test_settings_helpers(result):
    assert result["patch"] == {"prefer": "api", "fallback_enabled": True, "monthly_budget_usd": 75}
    assert result["patchEmpty"] == {"prefer": "subscription", "fallback_enabled": False, "monthly_budget_usd": None}
    assert "Settings → Providers" in result["noKey"]
    assert result["spent"].startswith("$1.50 of $200")


def test_page_wiring():
    page = (WEB / "chat_page.html").read_text(encoding="utf-8")
    assert page.index("chat_agent_billing.js") < page.index("js/chat/chat_page.js")
    settings = (WEB / "settings_page.html").read_text(encoding="utf-8")
    assert 'id="agentBillingEditor"' in settings and "settings_agent_billing.js" in settings
    assert "CuttleSettingsAgentBilling.mount" in settings
    slash = (WEB / "js/chat/chat_slash.js").read_text(encoding="utf-8")
    assert "prefix: '/use-api '" in slash and "prefix: '/use-subscription '" in slash
