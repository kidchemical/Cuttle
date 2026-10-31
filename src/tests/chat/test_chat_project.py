"""Chat project-context domain: lookups, reconcile decisions, outbound.

Behavioral characterization of src/web/js/chat/chat_project.js under node
(Phase 3 Slice 1). Covers the decision matrix behind reconcileChatProject
(server → prefs → stored → keep-unsaved-pick → backfills → default),
registry rebinds, outbound stamping, and message metadata mapping.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "chat/chat_project.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const P = require(process.env.MOD_JS);
const CUTTLE = { id: 4, name: 'Cuttle', path: 'C:\\\\Projects\\\\Cuttle\\\\src' };
const EP = { id: 7, name: 'Escape Purgatory', path: 'E:\\\\Projects\\\\DemoGame' };
const projects = [CUTTLE, EP];
const out = {};
out.byId = P.findProjectById(projects, '7');
out.byIdMissing = P.findProjectById(projects, 999);
out.byName = P.findProjectByName(projects, 'escape purgatory');
out.byPath = P.findProjectByPath(projects, 'e:/projects/demogame');
out.fromPathNested = P.projectFromPath('E:\\\\Projects\\\\DemoGame\\\\sub\\\\dir', projects);
out.fromPathUnknown = P.projectFromPath('/tmp/elsewhere', projects);
out.defaultStarred = P.findDefaultProject(projects, { id: 7, path: '', name: '' });
out.defaultPlain = P.findDefaultProject(projects, null);
out.defaultEmpty = P.findDefaultProject([], null);
out.storedFields = P.projectFieldsFromServerSession({ project_id: 7, project_name: '', project_path: '' });
out.storedNull = P.projectFieldsFromServerSession({});
out.storedObj = P.projectObjectFromStoredFields({ projectId: 7 }, projects);
out.storedNameOnly = P.projectObjectFromStoredFields({ projectName: 'Escape Purgatory' }, projects);
out.storedGhost = P.projectObjectFromStoredFields({ projectId: 9, projectName: 'Ghost', projectPath: 'C:\\\\Ghost' }, projects);
// reconcile matrix
const base = { projects, currentSessionId: 204, currentProject: null, starred: null };
out.serverWins = P.resolveChatProject(Object.assign({}, base, {
  serverFields: { projectId: 7, projectName: '', projectPath: '' } }));
out.prefsIdWins = P.resolveChatProject(Object.assign({}, base, {
  prefs: { projectId: 7 } }));
out.prefsIdMissStillStored = P.resolveChatProject(Object.assign({}, base, {
  prefs: { projectId: 999 } }));
out.prefsPathObj = P.resolveChatProject(Object.assign({}, base, {
  prefs: { projectPath: 'E:/Projects/DemoGame', projectName: 'EP' } }));
out.prefsNameBackfill = P.resolveChatProject(Object.assign({}, base, {
  prefs: { projectName: 'Escape Purgatory' } }));
out.storedIdWins = P.resolveChatProject(Object.assign({}, base, {
  stored: { projectId: 7 } }));
out.keepUnsaved = P.resolveChatProject({ projects, currentSessionId: null,
  currentProject: EP, starred: null });
out.defaultFallback = P.resolveChatProject(Object.assign({}, base, {}));
out.spaceNewChat = P.resolveChatProject({ projects, currentSessionId: null,
  currentProject: null, starred: { id: 4 }, spaceProject: { id: 7 } });
out.spaceIgnoredForSession = P.resolveChatProject(Object.assign({}, base, {
  starred: { id: 4 }, spaceProject: { id: 7 } }));
out.keepUnsavedBeatsSpace = P.resolveChatProject({ projects, currentSessionId: null,
  currentProject: CUTTLE, starred: { id: 4 }, spaceProject: { id: 7 } });
out.backfillPath = P.resolveChatProject(Object.assign({}, base, {
  prefs: { projectId: 7, projectName: 'Escape Purgatory' } }));
// stale name repaired from the live registry
out.rebindStale = P.rebindProjectToRegistry(
  { id: 7, name: 'Escape Purgatory', path: 'C:\\\\Projects\\\\Cuttle\\\\src' }, projects);
out.rebindClean = P.rebindProjectToRegistry(
  { id: 7, name: 'Escape Purgatory', path: 'E:\\\\Projects\\\\DemoGame' }, projects);
const body = {};
P.applyOutboundProjectToRequest(body, EP);
out.body = body;
out.key = P.currentProjectPathKey(EP);
out.msgOpts = P.projectFromMessageOpts({ project_id: 7 }, projects, null);
out.msgCwd = P.projectFromMessageOpts({ cursor_run: { cwd: 'E:\\\\Projects\\\\DemoGame' } }, projects, null);
out.msgChip = P.projectFromMessageOpts(
  { slash_command: { chips: [{ meta: 'cwd E:/Projects/DemoGame' }] } }, projects, null);
out.msgLive = P.projectFromMessageOpts({ live: true }, projects, EP);
out.msgNone = P.projectFromMessageOpts({}, projects, null);
// cwd / pending-changes resolution (Phase 3 slice 3E)
out.gitPathCheckoutWins = P.effectiveGitPath('/worktrees/branch', EP);
out.gitPathProjectOnly = P.effectiveGitPath('', EP);
out.gitPathNone = P.effectiveGitPath('', null);
out.pendingNoProject = P.pendingChangesProject(null, '/p', 'main', 'uid-1');
out.pendingLocal = P.pendingChangesProject(EP, 'E:\\\\Projects\\\\DemoGame', '', '');
out.pendingWorktreeBranch = P.pendingChangesProject(EP, '/worktrees/b', 'main', 'uid-1');
out.pendingWorktreeNoBranch = P.pendingChangesProject(EP, '/worktrees/b', '', 'uid-1');
out.pendingKeepsExtraFields = P.pendingChangesProject(
  Object.assign({}, EP, { type: 'local', starred: true }), 'E:\\\\Projects\\\\DemoGame', '', '');
const DISPLAY = [CUTTLE, Object.assign({}, EP, { display_name: 'EP' })];
out.chipDisplay = P.projectChipLabel({ id: 7, name: 'Old EP name', path: '' }, DISPLAY);
out.chipDisplayByPath = P.projectChipLabel({ id: null, name: '', path: EP.path }, DISPLAY);
out.chipFallbackName = P.projectChipLabel({ id: 4, name: 'Cuttle', path: '' }, DISPLAY);
out.chipUnregistered = P.projectChipLabel({ id: 99, name: 'Ghost', path: '' }, DISPLAY);
out.chipNone = P.projectChipLabel(null, DISPLAY);
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
def test_lookups_and_mappers():
    res = _run()
    assert res["byId"] == {"id": 7, "name": "Escape Purgatory", "path": "E:\\Projects\\DemoGame"}
    assert res["byIdMissing"] is None
    assert res["byName"]["id"] == 7
    assert res["byPath"]["id"] == 7
    assert res["fromPathNested"]["id"] == 7  # longest-prefix either direction
    assert res["fromPathUnknown"] == {"id": None, "name": "elsewhere", "path": "/tmp/elsewhere"}
    assert res["defaultStarred"]["id"] == 7
    assert res["defaultPlain"]["id"] == 4  # Cuttle-name fallback
    assert res["defaultEmpty"] is None
    assert res["storedFields"] == {
        "projectId": 7, "projectName": "", "projectPath": "", "checkoutUid": ""
    }
    assert res["storedNull"] is None
    assert res["storedObj"] == {"id": 7, "name": "Escape Purgatory", "path": "E:\\Projects\\DemoGame"}
    assert res["storedNameOnly"]["id"] == 7
    assert res["storedGhost"] == {"id": 9, "name": "Ghost", "path": "C:\\Ghost"}


@node_only
def test_project_chip_label_prefers_registry_display_name():
    res = _run()
    assert res["chipDisplay"] == "EP"
    assert res["chipDisplayByPath"] == "EP"
    assert res["chipFallbackName"] == "Cuttle"
    assert res["chipUnregistered"] == "Ghost"
    assert res["chipNone"] == ""


@node_only
def test_reconcile_priority_chain():
    res = _run()
    assert res["serverWins"] == {"proj": {"id": 7, "name": "Escape Purgatory",
                                          "path": "E:\\Projects\\DemoGame"}, "hadStored": True}
    assert res["prefsIdWins"]["proj"]["id"] == 7
    assert res["prefsIdWins"]["hadStored"] is True
    # Registry miss on prefs id still counts as stored (falls to default).
    assert res["prefsIdMissStillStored"]["hadStored"] is True
    assert res["prefsIdMissStillStored"]["proj"]["id"] == 4
    assert res["prefsPathObj"]["proj"]["path"] == "E:/Projects/DemoGame"
    assert res["prefsNameBackfill"]["proj"]["id"] == 7  # name repaired to registry
    assert res["storedIdWins"]["proj"]["id"] == 7
    # Unsaved new chat keeps the picked project instead of the default.
    assert res["keepUnsaved"] == {"proj": EP_OUT, "hadStored": False}
    assert res["defaultFallback"]["proj"]["id"] == 4
    assert res["spaceNewChat"]["proj"]["id"] == 7
    assert res["spaceIgnoredForSession"]["proj"]["id"] == 4
    assert res["keepUnsavedBeatsSpace"]["proj"]["id"] == 4
    assert res["backfillPath"]["proj"]["path"] == "E:\\Projects\\DemoGame"


EP_OUT = {"id": 7, "name": "Escape Purgatory", "path": "E:\\Projects\\DemoGame"}


@node_only
def test_rebind_and_outbound():
    res = _run()
    assert res["rebindStale"]["proj"]["path"] == "E:\\Projects\\DemoGame"
    assert res["rebindStale"]["changed"] is True
    assert res["rebindClean"]["changed"] is False
    assert res["body"] == {"project_id": 7, "project_name": "Escape Purgatory",
                           "project_path": "E:\\Projects\\DemoGame"}
    assert res["key"] == "E:\\Projects\\DemoGame"


@node_only
def test_message_opts_mapping():
    res = _run()
    assert res["msgOpts"]["id"] == 7
    assert res["msgCwd"]["id"] == 7
    assert res["msgChip"]["id"] == 7
    assert res["msgLive"] == EP_OUT
    assert res["msgNone"] is None


@node_only
def test_effective_git_path_resolution():
    """Git-facing cwd: bound checkout wins; else the project path; else ''."""
    res = _run()
    assert res["gitPathCheckoutWins"] == "/worktrees/branch"
    assert res["gitPathProjectOnly"] == "E:\\Projects\\DemoGame"
    assert res["gitPathNone"] == ""


@node_only
def test_pending_changes_project_snapshot():
    """Snapshot handed to the pending-changes panel / git viewers."""
    res = _run()
    assert res["pendingNoProject"] is None
    assert res["pendingLocal"] == {
        "id": 7, "name": "Escape Purgatory",
        "path": "E:\\Projects\\DemoGame", "worktree": "",
    }
    assert res["pendingWorktreeBranch"] == {
        "id": 7, "name": "Escape Purgatory",
        "path": "/worktrees/b", "worktree": "main",
    }
    # Bound checkout with no known branch labels the worktree with `true`.
    assert res["pendingWorktreeNoBranch"]["worktree"] is True
    # Extra registry fields on the project are preserved.
    assert res["pendingKeepsExtraFields"]["type"] == "local"
    assert res["pendingKeepsExtraFields"]["starred"] is True
    assert res["pendingKeepsExtraFields"]["path"] == "E:\\Projects\\DemoGame"
    assert res["pendingKeepsExtraFields"]["worktree"] == ""


@node_only
def test_chat_project_module_parses():
    import subprocess as sp
    proc = sp.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
