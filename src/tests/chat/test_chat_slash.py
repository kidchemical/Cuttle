"""Slash command domain: registry, parsing, matching, sticky/starred decisions.

Behavioral characterization of src/web/js/chat/chat_slash.js under node
(Phase 3 Slice 2). Covers parsing, exact/prefix matching, filtering,
ordering keys, duplicate handling, sticky/starred resolution, project
command merge, native control detection, chip classification, removal
indexes, and empty-input edges.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_slash.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const S = require(process.env.MOD_JS);
const out = {};
out.registrySize = S.SLASH_COMMANDS.length;
out.cursorSize = S.CURSOR_AGENT_SLASH_COMMANDS.length;
out.stickyPrefixes = S.SLASH_COMMANDS.filter((c) => c.stickySession).map((c) => c.prefix).sort();
out.controlPrefixes = S.SLASH_COMMANDS.filter((c) => c.controlCommand).map((c) => c.prefix).sort();
// parsing
out.parseCursor = S.parseStoredSlashCommandHead('/cursor do the thing');
out.parseBare = S.parseStoredSlashCommandHead('/cursor');
out.parseModel = S.parseStoredSlashCommandHead('/model', {});
out.parseUnknown = S.parseStoredSlashCommandHead('/frobnicate x');
out.parseEmpty = S.parseStoredSlashCommandHead('');
out.parseSkill = S.parseStoredSlashCommandHead('[Skill my-team/deploy] run it');
out.parseMulti = S.parseStoredSlashCommandMessage('/cursor /usage now');
out.parseBody = S.parseStoredSlashCommandMessage('just text');
out.parseTitle = S.parseTitleSlashChips('/cursor /model refresh extra words here', {});
out.normalize = S.normalizeSlashCommandStored({ label: 'X', meta: '/x' });
out.normalizeNull = S.normalizeSlashCommandStored(null);
out.meta = S.slashCommandMetaFromUserMessage('/codex hi');
out.projHead = S.parseProjectOrGenericSlashHead('/deploy prod', [
  { prefix: '/deploy ', label: 'Deploy', meta: '/deploy' }]);
out.cmdHead = S.parseProjectOrGenericSlashHead('/cmd lint --fix', []);
out.unknownHead = S.parseProjectOrGenericSlashHead('/zzz a b', []);
// matching
const item = { label: 'Cursor Agent', hint: 'Run Cursor agent CLI', prefix: '/cursor ',
  keywords: 'cursor agent', category: 'cursor' };
out.matchBare = S.slashPaletteItemMatches(item, '');
out.matchHit = S.slashPaletteItemMatches(item, 'agent');
out.matchMiss = S.slashPaletteItemMatches(item, 'zzz-nope');
out.matchCompact = S.slashPaletteItemMatches(
  { label: 'Claude Opus 4.6', prefix: '/x', keywords: '' }, 'opus4.6');
out.tokens = S.slashPaletteFilterTokens('  Aa  Bb ');
out.bucket = [S.slashPaletteTypeBucket('cursor-model'), S.slashPaletteTypeBucket('command')];
out.rankStarred = S.starredRank({ stickySession: true, prefix: '/cursor ' }, { prefixes: ['/cursor '] });
out.rankPlain = S.starredRank({ stickySession: true, prefix: '/cursor ' }, { prefixes: [] });
out.rankProj = S.starredRank({ category: 'project', projectId: 7 }, { prefixes: [], projectId: '7' });
out.rankProjPath = S.starredRank(
  { category: 'project', projectPath: 'E:\\\\Projects\\\\DemoGame' },
  { prefixes: [], projectPath: 'e:/projects/demogame' });
out.rankProjMiss = S.starredRank(
  { category: 'project', projectPath: '/tmp/other' },
  { prefixes: [], projectPath: 'e:/projects/demogame' });
// sticky
out.stickyBare = S.getStickySlashCommandFromMessage('/cursor').prefix;
out.stickyBody = S.getStickySlashCommandFromMessage('/codex do it').prefix;
out.stickyNone = S.getStickySlashCommandFromMessage('hello there');
out.stickyFail = S.isStickySlashAssistantFailure({ prefix: '/cursor ' }, { success: false });
out.stickyFailType = S.isStickySlashAssistantFailure({ prefix: '/cursor ' }, { type: 'cursor_error' });
out.stickyOk = S.isStickySlashAssistantFailure({ prefix: '/cursor ' }, { type: 'ok' });
out.overrideNone = S.stickyAgentOverrideForRequest({ message: '/cursor hi', hasChip: false, cleared: false, starredPrefixes: ['/cursor '] });
out.overrideCleared = S.stickyAgentOverrideForRequest({ message: 'hi', hasChip: false, cleared: true, starredPrefixes: [] });
out.overrideStar = S.stickyAgentOverrideForRequest({ message: 'hi', hasChip: false, cleared: false, starredPrefixes: ['/cursor '] });
out.overrideSilent = S.stickyAgentOverrideForRequest({ message: 'hi', hasChip: false, cleared: false, starredPrefixes: [] });
out.infer = S.inferStickyChipsFromUserMessages([{ role: 'user', content: '/muse build it' }]);
out.inferSkipParent = S.inferStickyChipsFromUserMessages(
  [{ role: 'user', content: '/cursor x', metadata: JSON.stringify({ speaker_kind: 'parent' }) }]);
out.assistantChips = S.stickyChipsFromAssistantSlash({ chips: [{ label: 'M', meta: '/muse', category: 'muse' }] });
out.hasChip = S.hasStickyAgentChip([{ prefix: '/cursor ' }], []);
out.hasChipNone = S.hasStickyAgentChip([{ prefix: '/usage' }], []);
out.activeChip = S.activeStickyAgentChip([], [{ prefix: '/codex ' }]);
// starred
out.isStarred = S.isSlashCommandStarred(['/cursor '], '/cursor ');
out.notStarred = S.isSlashCommandStarred(['/cursor '], '/codex ');
out.starChips = S.starredStickyChips(['/cursor ', '/nope']);
// native control
out.nativeRestart = S.isNativeControlCommand('/restart graceful');
out.nativeBare = S.isNativeControlCommand('restart');
out.nativeAgent = S.isNativeControlCommand('/cursor hi');
// project merge
out.projItems = S.buildProjectPaletteItems([{ id: 3, name: 'Demo', path: '/tmp/demo' }]);
out.projCmdItems = S.buildProjectCommandPaletteItems([
  { name: 'deploy', title: 'Deploy', description: 'd', aliases: ['dep'] },
  { name: '', title: 'Blank' },
  { name: 'lint', reserved_collision: true }]);
// classifiers
out.badge = S.isStickyAgentChip({ prefix: '/cursor ', category: 'cursor' }, 'cursor');
out.nested = S.isStickyAgentChip({ prefix: '/usage', category: 'cursor-cmd' }, 'cursor');
out.nestedUsage = S.isStickyAgentChip({ prefix: '/codex /usage', category: 'codex' }, 'codex');
out.headerAgent = S.isAgentHeaderChip({ prefix: '/cursor ', category: 'cursor' });
out.headerCmd = S.isAgentHeaderChip({ prefix: '/usage', category: 'cursor-cmd' });
out.cursorRelated = S.isCursorRelatedSlashChip({ category: 'cursor', prefix: '/cursor ' });
out.cursorNested = S.isCursorRelatedSlashChip({ category: 'cursor-cmd', prefix: '/usage' });
out.sorted = S.sortChipsAgentThenCommand(
  [{ label: 'b', category: 'command' }, { label: 'a', category: 'cursor' }]).map((c) => c.label);
out.agentId = S.composerChipAgentId({ category: 'codex-model' });
out.agentIdBare = S.composerChipAgentId({ category: 'command', prefix: '/usage' });
out.removal = S.composerChipRemovalIndexes(
  [{ category: 'cursor', prefix: '/cursor ' }, { category: 'cursor-model' }, { category: 'command' }], 0);
out.removalNested = S.composerChipRemovalIndexes(
  [{ category: 'claude', prefix: '/claude ' }, { category: 'claude-cmd', prefix: '/usage' }], 1);
out.removalSingle = S.composerChipRemovalIndexes([{ category: 'command' }], 0);
out.mergeAgents = S.mergeHarnessAgentsIntoSlashCommands(
  [{ prefix: '/cursor ', label: 'Cursor Agent', hint: 'h', category: 'cursor' }],
  [{ prefix: '/cursor ', label: 'Cursor!', available: false, install_hint: 'install it' },
   { prefix: '/new ', label: 'New', hint: 'new harness' }]);
process.stdout.write(JSON.stringify(out));
"""


def _run():
    import os
    proc = subprocess.run(
        ["node", "-e", HARNESS],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={"PATH": os.environ["PATH"], "MOD_JS": str(MOD_JS)},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@node_only
def test_registry_shape():
    res = _run()
    assert res["registrySize"] >= 10
    assert res["cursorSize"] >= 5
    assert "/cursor " in res["stickyPrefixes"]
    assert "/restart " in res["controlPrefixes"]


@node_only
def test_parsing_matrix():
    res = _run()
    assert res["parseCursor"] == {"chips": [{"label": "Cursor Agent", "meta": "/cursor",
                                             "category": "cursor"}], "body": "do the thing"}
    assert res["parseBare"] == {"chips": [{"label": "Cursor Agent", "meta": "/cursor",
                                           "category": "cursor"}], "body": ""}
    assert res["parseModel"]["chips"][0]["category"] == "cursor-cmd"
    assert res["parseModel"]["chips"][0]["meta"] == "/model"
    assert res["parseUnknown"] is None
    assert res["parseEmpty"] is None
    assert res["parseSkill"]["chips"][0]["category"] == "skill"
    assert [c["meta"] for c in res["parseMulti"]["chips"]] == ["/cursor", "/usage"]
    assert res["parseMulti"]["body"] == "now"
    assert res["parseBody"] is None
    assert res["normalize"] == {"chips": [{"label": "X", "meta": "/x", "category": "command"}]}
    assert res["normalizeNull"] is None
    assert res["meta"]["chips"][0]["meta"] == "/codex"
    assert res["projHead"] == {"chips": [{"label": "Deploy", "meta": "/deploy",
                                          "category": "project-cmd"}], "body": "prod"}
    assert res["cmdHead"]["chips"][0] == {"label": "lint", "meta": "/cmd lint",
                                         "category": "project-cmd"}
    assert res["unknownHead"]["chips"][0]["category"] == "command"


@node_only
def test_matching_and_rank():
    res = _run()
    assert res["matchBare"] is True
    assert res["matchHit"] is True
    assert res["matchMiss"] is False
    assert res["matchCompact"] is True
    assert res["tokens"] == ["aa", "bb"]
    assert res["bucket"] == ["cursor", "command"]
    assert res["rankStarred"] == 0
    assert res["rankPlain"] == 1
    assert res["rankProj"] == 0
    assert res["rankProjPath"] == 0
    assert res["rankProjMiss"] == 1


@node_only
def test_sticky_decisions():
    res = _run()
    assert res["stickyBare"] == "/cursor "
    assert res["stickyBody"] == "/codex "
    assert res["stickyNone"] is None
    assert res["stickyFail"] is True
    assert res["stickyFailType"] is True
    assert res["stickyOk"] is False
    assert res["overrideNone"] is None
    assert res["overrideCleared"] == "none"
    assert res["overrideStar"] == "none"
    assert res["overrideSilent"] is None
    assert res["infer"] == [{"prefix": "/muse ", "label": "Muse Code", "category": "muse"}]
    assert res["inferSkipParent"] == []
    assert res["assistantChips"] == [{"prefix": "/muse", "label": "M", "category": "muse"}]
    assert res["hasChip"] is True
    assert res["hasChipNone"] is False
    assert res["activeChip"] == {"prefix": "/codex "}


@node_only
def test_starred_and_control():
    res = _run()
    assert res["isStarred"] is True
    assert res["notStarred"] is False
    assert res["starChips"] == [{"prefix": "/cursor ", "label": "Cursor Agent",
                                 "category": "cursor"}]
    assert res["nativeRestart"] is True
    assert res["nativeBare"] is False
    assert res["nativeAgent"] is False


@node_only
def test_project_merge():
    res = _run()
    assert res["projItems"] == [{"category": "project", "prefix": "/project Demo ",
                                 "label": "Demo", "hint": "/tmp/demo", "meta": "/tmp/demo",
                                 "keywords": "project cd directory cwd folder Demo /tmp/demo",
                                 "projectId": 3, "projectPath": "/tmp/demo",
                                 "projectName": "Demo"}]
    assert [c["prefix"] for c in res["projCmdItems"]] == ["/deploy ", "/cmd lint "]
    assert res["projCmdItems"][0]["projectCommandName"] == "deploy"


@node_only
def test_classifiers_and_removal():
    res = _run()
    assert res["badge"] is True
    assert res["nested"] is False
    assert res["nestedUsage"] is False
    assert res["headerAgent"] is True
    assert res["headerCmd"] is False
    assert res["cursorRelated"] is True
    assert res["cursorNested"] is False
    assert res["sorted"] == ["a", "b"]  # stable partition, agent first
    assert res["agentId"] == "codex"
    assert res["agentIdBare"] == ""
    assert res["removal"] == [0, 1]
    assert res["removalSingle"] == [0]
    assert res["removalNested"] == [1]
    merged = res["mergeAgents"]
    assert merged[0]["label"] == "Cursor!"
    assert merged[0]["available"] is False
    assert merged[1]["prefix"] == "/new "


@node_only
def test_chat_slash_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
