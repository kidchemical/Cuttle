"""Composer lock decisions (src/web/js/chat/chat_composer_lock.js) under node.

Sub-agent child chats and supervised chats lock by default; a per-chat
unlock list (device-local) overrides it. The DOM controller is covered in
``e2e/test_chat_composer_lock.py``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LOCK_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_composer_lock.js"
SUP_MODEL_JS = REPO_ROOT / "src" / "web" / "js" / "chat" / "chat_supervision_model.js"

HARNESS = r"""
const L = require(process.env.LOCK_JS);
const S = require(process.env.SUP_MODEL_JS);
const many = [];
for (let i = 0; i < 205; i++) many.push(String(i));
console.log(JSON.stringify({
  child: L.isSubagentIdentity({parentSessionId: 7}),
  childZero: L.isSubagentIdentity({parentSessionId: 0}),
  orphan: L.isSubagentIdentity({parentSessionId: null, origin: 'subagent'}),
  plain: L.isSubagentIdentity({parentSessionId: null, origin: ''}),
  discord: L.isSubagentIdentity({parentSessionId: null, origin: 'discord'}),
  none: L.isSubagentIdentity(null),
  reasons: [L.lockReason({subagent: true, supervised: true}), L.lockReason({supervised: true}),
            L.lockReason({}), L.lockReason(null)],
  locked: [L.isLocked('subagent', false), L.isLocked('subagent', true), L.isLocked('', false)],
  parsed: [L.parseUnlocked('["1", 2]'), L.parseUnlocked('nope'), L.parseUnlocked('{"a":1}'),
           L.parseUnlocked(null)],
  added: L.withUnlocked(['1', '2'], '1', true),
  removed: L.withUnlocked(['1', '2'], '1', false),
  blank: L.withUnlocked(['1'], '', true),
  capped: L.withUnlocked(many, 'new', true),
  title: L.lockTitle('supervised'),
  captions: [L.lockCaption('supervised', 'CH-001581'), L.lockCaption('subagent', ''),
             L.lockCaption('supervised', '')],
  supBy: [S.supervisorOf({supervised_by: [{supervisor: 'CH-000009'}]}), S.supervisorOf({supervised_by: []}),
          S.supervisorOf(null)],
  sup: [S.isSupervised({supervised_by: [{id: 's'}]}), S.isSupervised({supervised_by: []}),
        S.isSupervised({supervising: [{id: 's'}]}), S.isSupervised(null)],
}));
"""


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, "-e", HARNESS], capture_output=True, text=True, encoding="utf-8", timeout=30,
                         env={**os.environ, "LOCK_JS": str(LOCK_JS), "SUP_MODEL_JS": str(SUP_MODEL_JS)})
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_subagent_identity_from_parent_or_origin():
    r = _run()
    assert r["child"] is True and r["childZero"] is True and r["orphan"] is True
    assert r["plain"] is False and r["discord"] is False and r["none"] is False


def test_reason_and_lock_decision():
    r = _run()
    assert r["reasons"] == ["subagent", "supervised", "", ""]
    assert r["locked"] == [True, False, False]
    assert "supervised chat" in r["title"] and "chat menu" in r["title"]


def test_unlock_list_parse_and_update():
    r = _run()
    assert r["parsed"] == [["1", "2"], [], [], []]
    assert r["added"] == ["2", "1"]
    assert r["removed"] == ["2"]
    assert r["blank"] == ["1"]
    assert len(r["capped"]) == 200 and r["capped"][-1] == "new" and r["capped"][0] == "6"


def test_supervised_only_when_another_chat_supervises_this_one():
    # A supervisor chat (supervising, not supervised_by) is not locked.
    assert _run()["sup"] == [True, False, False, False]


def test_caption_names_the_controlling_chat():
    r = _run()
    sup, sub, anon = r["captions"]
    assert sup["handle"] is True and sup["who"] == "CH-001581"
    assert "supervising this chat" in sup["rest"] and "☰" in sup["rest"]
    assert sub["handle"] is False and sub["who"] == "Its parent chat" and "sub-agent" in sub["rest"]
    assert anon["who"] == "Another chat"
    assert r["supBy"] == ["CH-000009", "", ""]
