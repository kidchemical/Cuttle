"""Working badges describe the turn, even when next-send preferences disagree.

The production DOM lifecycle is exercised in e2e/test_chat_badge_identity.py.
These decision cases deliberately supply conflicting composer identities.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / 'web/js/chat/chat_agent_model.js'


@pytest.mark.skipif(shutil.which('node') is None, reason='node unavailable')
@pytest.mark.parametrize('agent', ['cursor', 'muse', 'codex', 'claude', 'hermes', 'opencode', 'antigravity', 'deepseek'])
def test_turn_identity_survives_conflicting_composer_and_steers(agent):
    script = r'''
const A = require(process.argv[1]);
const agent = process.argv[2];
const sent = {chips: [{label: agent + ' - frozen model · medium', meta: '/' + agent, category: agent}]};
let composer = {chips: [{label: 'Claude - Opus 5.5', meta: '/claude'}]};
const history = [
  {role: 'assistant', metadata: {slash_command: composer}},
  {role: 'user', metadata: JSON.stringify({slash_command: sent})},
  {role: 'user', metadata: {steered: true, slash_command: composer}},
  {role: 'user', metadata: {speaker_kind: 'parent', slash_command: composer}},
];
const before = A.turnSlashFromMessages(history);
composer = {chips: []}; // removal is a next-send choice
const reopened = A.turnSlashFromMessages(history);
const live = {chips: [{label: 'actual fallback', meta: '/cursor'}]};
const fallback = A.turnSlashFromMessages(history, {active: true, slash_command: live});
const plain = A.turnSlashFromMessages(history.concat({role: 'user', content: 'route me'}));
process.stdout.write(JSON.stringify({before, reopened, fallback, plain,
  empty: A.turnSlashFromMessages([]) === undefined}));
'''
    proc = subprocess.run(['node', '-e', script, str(MODULE), agent], capture_output=True, text=True, check=True)
    result = json.loads(proc.stdout)
    assert result['before'] == result['reopened']
    assert result['before']['chips'][0]['category'] == agent
    assert result['fallback']['chips'][0]['label'] == 'actual fallback'
    assert result['plain'] is None
    assert result['empty'] is True


@pytest.mark.skipif(shutil.which('node') is None, reason='node unavailable')
def test_shared_selection_rejects_stale_poll_and_preserves_pending_pick():
    script = '''
const A = require(process.argv[1]);
const selection = revision => ({stickyChips: [{prefix:'/codex '}], revision});
process.stdout.write(JSON.stringify([
 A.shouldAdoptComposerSelection(selection(4), {revision:5}),
 A.shouldAdoptComposerSelection(selection(6), {revision:5, pending:1}),
 A.shouldAdoptComposerSelection(selection(6), {revision:5, pending:0}),
 A.shouldAdoptComposerSelection({stickyChips:[], stickyCleared:true, revision:7}, {revision:6}),
]));
'''
    result = subprocess.run(['node', '-e', script, str(MODULE)], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [False, False, True, True]
