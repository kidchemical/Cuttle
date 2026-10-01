"""Project chip must stick after /project — CH-000204 regression.

Reproduces: chat already stamped Cuttle in the auth DB / last messages hydrate.
User picks Escape Purgatory via the composer chip. A later reconcile (send path,
loadProjects, session reload) snaps the chip back to Cuttle because
``lastHydratedSessionProject`` still says Cuttle and beats fresh local prefs.

Runs the real helpers from chat_page.js under Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"
CHAT_PROJECT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_project.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

CUTTLE = {
    "id": 4,
    "name": "Cuttle",
    "path": r"C:\Projects\Cuttle\src",
}
EP = {
    "id": 7,
    "name": "Demo Game",
    "path": r"E:\Projects\DemoGame",
}


def _extract(src: str, start_marker: str, end_marker: str) -> str:
    head = src.index(start_marker)
    tail = src.index(end_marker, head)
    return src[head:tail]


def _run_project_chip_js(script: str) -> dict:
    # Phase 3 Slice 1: project decision logic lives in chat_project.js and
    # is required directly (no source slicing — slicing is what rotted these
    # tests). Chat-page orchestration shells are still sliced by markers.
    src = CHAT_JS.read_text(encoding="utf-8")
    mod_path = str(CHAT_PROJECT_JS).replace("\\", "\\\\")

    id_helpers = _extract(
        src,
        "    function toAuthDbSessionId(sessionId) {",
        "    /**\n     * Human-friendly chat id shown in the corner badge",
    )
    prefs_map = _extract(
        src,
        "    const SESSION_PREFS_STORAGE_KEY = 'cuttleChatSessionPrefs';",
        "    function clearSessionPrefs(sessionId) {",
    )
    session_lookup = _extract(
        src,
        "    function findAuthServerSession(sessionId) {",
        "    function resolveSessionProjectInfo(sessionId, sessionObj) {",
    )
    stored_check = _extract(
        src,
        "    function sessionHasStoredProject(sessionId, sessionObj) {",
        "    function isHistoryProjectSectionExpanded(",
    )
    set_chat = _extract(
        src,
        "    /**\n"
        "     * Keep the in-memory auth session + last /messages hydrate in sync with the\n",
        "    // --- Pending changes strip",
    )
    reconcile = _extract(
        src,
        "    /** Resolve the chat's project from the active session, falling back to the Cuttle default. */",
        "    async function switchChatProject() {",
    )
    persist = _extract(
        src,
        "    function persistProjectForCurrentSession(opts) {",
        "    function handleSlashKeyDown(event, textarea) {",
    )

    harness = f"""
const CuttleChatProject = require({json.dumps(mod_path)});
const localStore = {{}};
const localStorage = {{
    getItem: (k) => (Object.prototype.hasOwnProperty.call(localStore, k) ? localStore[k] : null),
    setItem: (k, v) => {{ localStore[k] = String(v); }},
    removeItem: (k) => {{ delete localStore[k]; }},
}};
const window = {{
    CuttleAuth: {{
        isAuthenticated: () => true,
        getSessions: () => authSessions,
    }},
}};
const inAppShell = false;
let authSessions = [
    {{
        id: 204,
        project_id: {json.dumps(CUTTLE["id"])},
        project_name: {json.dumps(CUTTLE["name"])},
        project_path: {json.dumps(CUTTLE["path"])},
    }},
];
let projects = {json.dumps([CUTTLE, EP])};
let currentSessionId = 204;
let currentProject = {json.dumps(CUTTLE)};
let lastHydratedSessionProject = {{
    id: '204',
    project_id: {json.dumps(CUTTLE["id"])},
    project_name: {json.dumps(CUTTLE["name"])},
    project_path: {json.dumps(CUTTLE["path"])},
}};
function isAuthMode() {{ return true; }}
function readStarredProject() {{ return null; }}
function findProjectById(id) {{ return CuttleChatProject.findProjectById(projects, id); }}
function findDefaultProject() {{ return CuttleChatProject.findDefaultProject(projects, readStarredProject()); }}
function projectFieldsFromServerSession(s) {{ return CuttleChatProject.projectFieldsFromServerSession(s); }}
function renderProjectChips() {{}}
function refreshTypingIndicatorProjectChip() {{}}
function schedulePendingChangesRefresh() {{}}
function reportProjectToShell() {{}}
function refreshChatWidgets() {{}}
function loadProjectCommandsForPalette() {{}}
function loadHarnessAgentsForPalette() {{}}
function addSystemMessage() {{}}
const fetch = () => Promise.resolve({{ ok: true, json: async () => ({{ success: true }}) }});
{id_helpers}
{prefs_map}
{session_lookup}
{stored_check}
{persist}
{set_chat}
{reconcile}
{script}
"""
    proc = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr + "\n---\n" + proc.stdout
    return json.loads(proc.stdout)


@node_only
def test_project_chip_survives_reconcile_after_user_pick():
    """CH-000204: /project → Escape Purgatory must not snap back to Cuttle on reconcile."""
    res = _run_project_chip_js(
        """
const ep = projects.find((p) => p.name === 'Demo Game');
setChatProject(ep, { silent: true });
// Send path / loadProjects / session settle all call this.
reconcileChatProject();
const outbound = resolveOutboundProject();
process.stdout.write(JSON.stringify({
    afterSet: {
        name: currentProject && currentProject.name,
        path: currentProject && currentProject.path,
        id: currentProject && currentProject.id,
    },
    outbound: {
        name: outbound && outbound.name,
        path: outbound && outbound.path,
        id: outbound && outbound.id,
    },
    hydrated: lastHydratedSessionProject,
    prefs: getSessionPrefs(204),
}));
"""
    )
    assert res["afterSet"]["name"] == "Demo Game", res
    assert res["outbound"]["name"] == "Demo Game", res
    assert "Cuttle" not in (res["outbound"]["path"] or ""), res
    assert res["outbound"]["path"] == EP["path"], res


@node_only
def test_set_chat_project_updates_hydrated_snapshot():
    """User pick must refresh lastHydrated so server-first reconcile cannot resurrect Cuttle."""
    res = _run_project_chip_js(
        """
const ep = projects.find((p) => p.name === 'Demo Game');
setChatProject(ep, { silent: true });
process.stdout.write(JSON.stringify({
    hydrated: lastHydratedSessionProject,
    authRow: authSessions[0],
    current: currentProject,
}));
"""
    )
    assert res["current"]["name"] == "Demo Game", res
    assert res["hydrated"]["project_name"] == "Demo Game", res
    assert res["hydrated"]["project_path"] == EP["path"], res
    assert res["authRow"]["project_name"] == "Demo Game", res
    assert res["authRow"]["project_path"] == EP["path"], res
