"""Removing the starred agent badge must actually remove the agent.

Reproduces CH-000141: a new chat seeded with the starred "Cursor Agent - Auto"
badge, the badge removed by the user, then "test" sent.

1. The turn still ran on Cursor — `/api/chat` re-applied the star server-side,
   so Cuttle's agent router never saw the prompt.
2. Refreshing brought the badge back, because the persisted user turn had been
   rewritten to "/cursor test" and the composer infers the badge from history.

Covers both layers: the server-side star fallback (`starred_slash`) and the
chat composer (real chat_page.js source run under Node).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api.auth_db import AuthDatabase
from api import starred_slash as ss

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_page.js"
SLASH_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_slash.js"


# --------------------------------------------------------------------------
# Server: /api/chat must honour an explicit "no agent" from the client
# --------------------------------------------------------------------------


def test_no_agent_beats_the_star_on_a_fresh_chat(tmp_path: Path, monkeypatch):
    """CH-000141: badge removed on a brand-new chat, plain "test" sent."""
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    fresh = db.create_chat_session(owner, "brand new")

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: ["/cursor "])

    # Without the override the star still seeds a fresh chat.
    assert ss.apply_default_sticky_prefix(
        "test", fresh, star_on_new_session_only=True
    ) == "/cursor test"
    # The user removed the badge: this turn belongs to the router.
    assert ss.apply_default_sticky_prefix(
        "test", fresh, star_on_new_session_only=True, no_agent=True
    ) == "test"


def test_no_agent_beats_session_history(tmp_path: Path, monkeypatch):
    """Dropping the badge mid-chat leaves the earlier /cursor turns behind."""
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "cursor chat")
    db.add_message(sid, "user", "/cursor fix the build")
    db.add_message(sid, "assistant", "done")

    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: [])

    assert ss.apply_default_sticky_prefix("and now?", sid) == "/cursor and now?"
    assert ss.apply_default_sticky_prefix("and now?", sid, no_agent=True) == "and now?"


def test_explicit_agent_prefix_still_wins_over_no_agent(monkeypatch):
    """A typed /cursor is the user's choice too — never strip it."""
    monkeypatch.setattr(ss, "get_starred_prefixes", lambda: ["/cursor "])
    monkeypatch.setattr(ss, "infer_session_sticky_prefix", lambda _sid: None)
    assert ss.apply_default_sticky_prefix("/cursor go", no_agent=True) == "/cursor go"


@pytest.mark.parametrize(
    "payload",
    [
        {"sticky_agent": "none"},
        {"sticky_agent": "None"},
        {"stickyAgent": "off"},
        {"sticky_agent": False},
        {"agent": "none"},
    ],
)
def test_request_payloads_that_mean_no_agent(payload):
    assert ss.is_no_agent_request(payload) is True


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"message": "hi"},
        {"sticky_agent": ""},
        {"sticky_agent": "/cursor "},
        {"agent": "cursor"},
    ],
)
def test_request_payloads_that_keep_the_star(payload):
    """A client that says nothing must keep the star fallback (LAN, API callers)."""
    assert ss.is_no_agent_request(payload) is False


# --------------------------------------------------------------------------
# Composer: chat_page.js sticky-agent helpers, run under Node
# --------------------------------------------------------------------------

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


def _run_sticky_js(script: str, *, starred, chips, session_id=None, prefs=None):
    """Run the real sticky-agent helpers from chat_page.js with the UI stubbed."""
    src = CHAT_JS.read_text(encoding="utf-8")
    # Registry lives in chat_slash.js (Phase 3 Slice 2); the require below
    # also serves the delegating wrappers in the helpers range.
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    slash_mod = str(SLASH_JS)
    commands = _extract(slash_src, "const SLASH_COMMANDS = [", "\n];") + "\n];"
    helpers = _extract(
        src,
        "    function getStickySlashCommandFromMessage(message) {",
        "    function persistProjectForCurrentSession(opts)",
    )

    draft_restore = _extract(
        src, "    function restoreComposerDraftControls(sid) {",
        "    function saveComposerDraft(sid, composerKey) {",
    )
    harness = f"""
const CuttleChatComposer = require("{CHAT_JS.parent / 'chat_composer.js'}");
const CuttleChatAgentModel = require("{CHAT_JS.parent / 'chat_agent_model.js'}");
const slashPaletteSupplement = {{}};
const newComposerPrefsId = 'draft:new';
function beginSupplementFetch(field) {{ slashPaletteSupplement[field] = 1; }}
const CuttleChatSlash = require("{slash_mod}");
{commands}
const prefsMap = {json.dumps(prefs or {})};
let currentSessionId = {json.dumps(session_id)};
const slashCtx = {{
    chat: {{ chips: {json.dumps(chips)} }},
    welcome: {{ chips: {json.dumps(chips)} }},
}};
function getSessionPrefs(id) {{
    if (id == null || id === '') return null;
    return prefsMap[String(id)] || null;
}}
function updateSessionPrefs(id, patch) {{
    if (id == null || id === '') return;
    prefsMap[String(id)] = Object.assign({{}}, prefsMap[String(id)] || {{}}, patch || {{}});
}}
function readStarredSlashPrefixes() {{ return {json.dumps(starred)}; }}
function renderSlashChips() {{}}
function refreshTypingIndicatorHeaderBadges() {{}}
const document = {{ getElementById: () => null }};
{draft_restore}
{helpers}
{script}
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


CURSOR_CHIP = {"prefix": "/cursor ", "label": "Cursor Agent", "category": "command"}
MODEL_CHIP = {"prefix": "/model auto", "label": "Auto", "category": "cursor-model"}

_REPORT_OVERRIDE = """
const out = stickyAgentOverrideForRequest(MESSAGE);
process.stdout.write(JSON.stringify({ override: out === undefined ? null : out }));
"""


@node_only
def test_composer_tells_the_server_the_badge_was_removed():
    """Star known, no badge on the composer → this turn is the router's."""
    res = _run_sticky_js(
        _REPORT_OVERRIDE.replace("MESSAGE", '"test"'), starred=["/cursor "], chips=[]
    )
    assert res["override"] == "none"


@node_only
def test_composer_keeps_the_agent_when_the_badge_is_present():
    res = _run_sticky_js(
        _REPORT_OVERRIDE.replace("MESSAGE", '"test"'),
        starred=["/cursor "],
        chips=[CURSOR_CHIP],
    )
    assert res["override"] is None


@node_only
def test_typed_agent_command_never_sends_the_override():
    res = _run_sticky_js(
        _REPORT_OVERRIDE.replace("MESSAGE", '"/cursor test"'),
        starred=["/cursor "],
        chips=[],
    )
    assert res["override"] is None


@node_only
def test_unknown_star_leaves_the_server_fallback_alone():
    """No star in localStorage yet (hydration race) → do not override."""
    res = _run_sticky_js(
        _REPORT_OVERRIDE.replace("MESSAGE", '"test"'), starred=[], chips=[]
    )
    assert res["override"] is None


@node_only
def test_cleared_session_overrides_even_without_a_star():
    res = _run_sticky_js(
        _REPORT_OVERRIDE.replace("MESSAGE", '"test"'),
        starred=[],
        chips=[],
        session_id="CH-000141",
        prefs={"CH-000141": {"stickyChips": [], "stickyCleared": True}},
    )
    assert res["override"] == "none"


@node_only
def test_model_chip_alone_is_not_an_agent_badge():
    res = _run_sticky_js(
        _REPORT_OVERRIDE.replace("MESSAGE", '"test"'),
        starred=["/cursor "],
        chips=[MODEL_CHIP],
    )
    assert res["override"] == "none"


@node_only
def test_refresh_does_not_resurrect_a_removed_badge():
    """Issue 2: history still holds /cursor turns, but the user removed the badge."""
    script = """
const messages = [
    { role: 'user', content: '/cursor test' },
    { role: 'assistant', content: 'hi' },
];
restoreSessionStickySlash('CH-000141', messages);
process.stdout.write(JSON.stringify({ chips: slashCtx.chat.chips }));
"""
    res = _run_sticky_js(
        script,
        starred=["/cursor "],
        chips=[],
        session_id="CH-000141",
        prefs={"CH-000141": {"stickyChips": [], "stickyCleared": True}},
    )
    assert res["chips"] == []


@node_only
def test_refresh_still_recovers_a_badge_that_was_never_removed():
    """Guard the existing recovery path for wiped prefs."""
    script = """
const messages = [
    { role: 'user', content: '/cursor test' },
    { role: 'assistant', content: 'hi' },
];
restoreSessionStickySlash('CH-000200', messages);
process.stdout.write(JSON.stringify({ chips: slashCtx.chat.chips }));
"""
    res = _run_sticky_js(
        script, starred=["/cursor "], chips=[], session_id="CH-000200", prefs={}
    )
    assert [c["prefix"] for c in res["chips"]] == ["/cursor "]


@node_only
def test_removing_the_badge_is_remembered_for_the_session():
    script = """
markStickyAgentCleared(true);
const before = JSON.parse(JSON.stringify(prefsMap));
const overrideBefore = stickyAgentOverrideForRequest('test');
markStickyAgentCleared(false);
process.stdout.write(JSON.stringify({
    prefs: before,
    overrideBefore: overrideBefore === undefined ? null : overrideBefore,
    overrideAfter: stickyAgentOverrideForRequest('test') === 'none' ? 'none' : null,
    cleared: isStickyAgentCleared(),
}));
"""
    res = _run_sticky_js(
        script, starred=[], chips=[], session_id="CH-000141", prefs={}
    )
    assert res["prefs"]["CH-000141"]["stickyCleared"] is True
    assert res["overrideBefore"] == "none"
    assert res["cleared"] is False
