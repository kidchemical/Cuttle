"""A ``?chat=`` restore that gives up must not drop the pane's chat handle
while the user is signed out.

Regression: a refresh with an expired/revoked session cookie let
``bootRestoreSession`` time out, show the welcome splash and call
``syncChatUrl(null)``. That posts ``cuttle-chat-url {chatId: null}`` to the
shell, which persists ``/chat_page.html?new=1`` for the pane — so every pane
of the active space's saved layout was rewritten to a blank chat before the
user could sign back in. Signed out, the handle must stay (the page's
auth-changed handler opens it after sign-in); signed in but slow keeps the
old give-up behavior.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_session.js"
CHAT_PAGE_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

HARNESS = r"""
const S = require(process.env.MOD);
global.setTimeout = (fn) => 0;  // veil safety timer: irrelevant here

async function run(authed) {
  const calls = [];
  const rec = (name) => (...a) => { calls.push([name].concat(a)); };
  const host = {
    storage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    clearChatRestoreVeil: rec('clearChatRestoreVeil'),
    takeChatTranscriptPrefetch: () => null,
    waitForAuthReady: () => Promise.resolve(false),
    isAuthenticated: () => authed,
    firstTruthy: () => Promise.resolve(null),
    loadChatSession: rec('loadChatSession'),
    showWelcomeSplash: rec('showWelcomeSplash'),
    showToast: rec('showToast'),
  };
  const boot = S.createSessionBoot(S.create(), host);
  await boot.bootRestoreSession('2006');
  return { calls, abandoned: boot.isRestoreAbandoned() };
}

(async () => {
  const out = { signedOut: await run(false), signedInSlow: await run(true) };
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run():
    proc = subprocess.run(
        ["node", "-e", HARNESS], capture_output=True, text=True, timeout=60,
        env={"PATH": os.environ["PATH"], "MOD": str(MOD)},
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_signed_out_restore_keeps_chat_handle_and_stays_pending():
    res = _run()["signedOut"]
    splash = [c for c in res["calls"] if c[0] == "showWelcomeSplash"]
    assert splash == [["showWelcomeSplash", {"keepChatUrl": True}]]
    assert not [c for c in res["calls"] if c[0] == "showToast"]
    # Not abandoned: the auth-changed handler may still open the chat.
    assert res["abandoned"] is False


def test_signed_in_slow_restore_still_gives_up():
    res = _run()["signedInSlow"]
    names = [c[0] for c in res["calls"]]
    assert ["showWelcomeSplash"] == [c[0] for c in res["calls"] if c[0] == "showWelcomeSplash"]
    assert res["calls"][names.index("showWelcomeSplash")][1:] == []
    assert "showToast" in names
    assert res["abandoned"] is True


def test_page_splash_honors_keep_chat_url():
    src = CHAT_PAGE_JS.read_text(encoding="utf-8")
    start = src.index("function showWelcomeSplash(opts)")
    body = src[start:start + 1600]
    assert "if (!(opts && opts.keepChatUrl)) syncChatUrl(null);" in body
    assert "isAuthenticated: isAuthMode," in src
