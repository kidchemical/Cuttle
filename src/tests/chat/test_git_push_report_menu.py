"""Git push menu/action owner: CuttleGitPushReport.create decisions.

Characterization of the git push menu family moved out of chat_page.js in
Phase 3 slice 3E pt2. The controller owns the repo cache, the multi-repo menu
mode and the submenu HTML; the page injects project identity, the
pending-changes snapshot, fetch and DOM/toast effects. These tests pin the
owner's public decisions under node (no DOM, no network).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MOD_JS = REPO_ROOT / "src" / "web" / "js" / "git" / "git_push_report.js"

node_only = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)

HARNESS = """
const R = require(process.env.MOD_JS);
const out = {};
out.hasCreate = typeof R.create === 'function';
const multi = R.create({
  getProjectPath: () => '/proj',
  getPendingRepos: () => [
    { repo_root: '/proj/a', branch: 'main', label: 'a' },
    { repo_root: '/proj/b', branch: 'dev', label: 'b' },
  ],
});
out.api = {
  menuHtml: typeof multi.menuHtml, refreshMenuMode: typeof multi.refreshMenuMode,
  openSubmenu: typeof multi.openSubmenu, push: typeof multi.push,
  setupConfirmModal: typeof multi.setupConfirmModal,
};
out.multiTrue = multi.isMultiRepo(multi.knownRepos());
out.htmlMulti = multi.menuHtml({ gitPush: '<svg id="gp"></svg>' });

const one = R.create({
  getProjectPath: () => '/proj',
  getPendingRepos: () => [{ repo_root: '/proj/a', branch: 'main', label: 'a' }],
});
out.multiFalse = one.isMultiRepo(one.knownRepos());
out.htmlSingle = one.menuHtml({ gitPush: '<svg id="gp"></svg>' });

const none = R.create({ getProjectPath: () => '', getPendingRepos: () => [] });
out.knownNone = none.knownRepos();

(async () => {
  let calls = 0;
  let lastUrl = '';
  const f = R.create({
    getProjectPath: () => '/proj',
    getPendingRepos: () => [],
    fetch: async (url) => {
      calls += 1; lastUrl = url;
      return { json: async () => ({ success: true, repos: [
        { repo_root: '/proj/a' }, { repo_root: '/proj/b' } ] }) };
    },
  });
  const repos = await f.fetchRepos(true);
  out.fetchCount = repos.length;
  out.fetchCalls = calls;
  out.fetchUrl = lastUrl;
  out.fetchMiss = await R.create({
    getProjectPath: () => '', getPendingRepos: () => [], fetch: async () => { throw new Error('nope'); },
  }).fetchRepos(true);
  // force=false prefers the 15s cache: no second fetch.
  await f.fetchRepos(false);
  out.fetchCallsAfterCached = calls;
  process.stdout.write(JSON.stringify(out));
})();
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
def test_owner_exposes_menu_interface():
    res = _run()
    assert res["hasCreate"] is True
    assert res["api"] == {
        "menuHtml": "function",
        "refreshMenuMode": "function",
        "openSubmenu": "function",
        "push": "function",
        "setupConfirmModal": "function",
    }


@node_only
def test_menu_mode_from_known_repos():
    res = _run()
    assert res["multiTrue"] is True
    assert 'data-git-push-multi="1"' in res["htmlMulti"]
    assert 'aria-haspopup="true"' in res["htmlMulti"]
    assert "chat-chrome-menu-caret" in res["htmlMulti"]
    assert res["multiFalse"] is False
    # Single repo omits the multi attribute, caret and aria-haspopup.
    assert "data-git-push-multi" not in res["htmlSingle"]
    assert "chat-chrome-menu-caret" not in res["htmlSingle"]
    assert 'data-git-push-trigger="1"' in res["htmlSingle"]
    # No bound project path -> no known repos.
    assert res["knownNone"] is None


@node_only
def test_fetch_uses_host_capability_and_cache():
    res = _run()
    assert res["fetchCount"] == 2
    assert res["fetchCalls"] == 1
    assert "path=%2Fproj" in res["fetchUrl"]
    assert res["fetchMiss"] == []
    # force=false reused the 15s cache instead of re-fetching.
    assert res["fetchCallsAfterCached"] == 1


@node_only
def test_module_parses():
    proc = subprocess.run(["node", "--check", str(MOD_JS)], capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
