"""Agent review on the Git surfaces in a real browser: the shared Pending
Changes strip, the diff modal and the commit guard.

A stub page hosts the real ``pending_changes_panel.js`` / ``diff_modal.js`` /
``agent_review_client.js``; ``/api/git/*``, ``/api/agent-review/*``, the
agent-review setting and the experimental flags are answered in-process by
the real Flask app over a temporary git project. Agent turns go through the
real harness seams (no CLI runs). The commit-message suggestion is stubbed
(it would call a model); the review stream is not forwarded, so the page
uses its polling fallback.
"""

from __future__ import annotations

import subprocess
import time
from urllib.parse import urlparse

import pytest

from tests.e2e.test_ide_agent_review import AGENT, ORIGINAL, agent_turn  # noqa: F401  (fixture)
from tests.e2e.test_ide_app import WEB, browser, flask_client, project  # noqa: F401  (fixtures)

ORIGIN = "http://git.test"
FORWARD = ("/api/git/", "/api/agent-review/", "/api/settings/agent-review", "/api/experimental/flags")

STUB = """<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/css/pending_changes.css">
<link rel="stylesheet" href="/css/diff_modal.css">
<link rel="stylesheet" href="/css/agent_review.css">
</head><body style="background:#111;color:#eee">
<div id="host"></div>
<script src="/js/git/agent_review_client.js"></script>
<script src="/js/git/pending_changes_panel.js"></script>
<script src="/js/git/diff_modal.js"></script>
<script>
window.project = {path: PROJECT_PATH, name: 'Demo'};
window.panel = CuttlePendingChangesPanel.create({
  host: document.getElementById('host'),
  getProject: () => window.project,
  startExpanded: true,
  onOpenDiff: (rel, meta) => CuttleDiffModal.open({projectPath: window.project.path, repoRoot: meta.repoRoot, file: rel}),
});
window.panel.refresh();
</script></body></html>"""


@pytest.fixture()
def page(browser, flask_client, project):
    context = browser.new_context(viewport={"width": 1300, "height": 900})
    pg = context.new_page()
    pg.errors = []
    pg.on("pageerror", lambda exc: pg.errors.append(str(exc)))

    def handle(route):
        req = route.request
        parsed = urlparse(req.url)
        path = parsed.path
        if parsed.netloc != "git.test":
            return route.abort()
        if path == "/stub.html":
            return route.fulfill(body=STUB.replace("PROJECT_PATH", repr(str(project))), content_type="text/html")
        if path == "/api/git/suggest-commit-message":
            return route.fulfill(json={"success": True, "message": "Agent work"})
        if path.startswith(FORWARD) and path != "/api/agent-review/stream":
            res = flask_client.open(path, method=req.method, query_string=parsed.query,
                                    data=req.post_data_buffer, content_type=req.headers.get("content-type"))
            return route.fulfill(status=res.status_code, body=res.get_data(),
                                 headers={"content-type": res.headers.get("content-type", "application/json")})
        if path.startswith("/api/"):
            return route.fulfill(json={"success": False, "error": "not in this test"}, status=404)
        target = (WEB / path.lstrip("/")).resolve()
        if not target.is_file() or not target.is_relative_to(WEB):
            return route.fulfill(status=404, body="missing")
        ctype = {".js": "application/javascript", ".css": "text/css"}.get(target.suffix, "text/plain")
        return route.fulfill(body=target.read_bytes(), content_type=ctype)

    pg.route("**/*", handle)
    yield pg
    context.close()


def _row(rel):
    return f".pending-changes-row[data-rel-path='{rel}']"


def _wait(page, predicate, timeout=6):
    """Poll while pumping Playwright: routes answer requests on this thread."""
    deadline = time.time() + timeout
    while time.time() < deadline and not predicate():
        page.wait_for_timeout(100)
    return predicate()


def _commits(project):
    return int(subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=project, capture_output=True, text=True).stdout)


def test_pending_strip_badges_keep_and_undo(agent_turn, page, project):
    agent_turn({"src/app.py": AGENT, "notes.md": "agent notes\n"})
    page.goto(ORIGIN + "/stub.html")
    page.wait_for_selector(_row("src/app.py") + " .pending-changes-review-badge", timeout=8000)
    assert page.inner_text(_row("src/app.py") + " .pending-changes-review-badge") == "✦ 2"
    assert page.inner_text("[data-pc='review']") == "✦ Review 3"
    # Keep: badge goes, disk untouched.
    page.click(_row("notes.md") + " .pending-changes-review-keep")
    page.wait_for_selector(_row("notes.md") + " .pending-changes-review-badge", state="detached", timeout=8000)
    assert (project / "notes.md").read_text(encoding="utf-8") == "agent notes\n"
    # Undo asks first, then rewrites the file and the row leaves the strip.
    page.click(_row("src/app.py") + " .pending-changes-review-undo")
    page.click(".cuttle-review-dialog button[value='ok']")
    assert _wait(page, lambda: (project / "src" / "app.py").read_text(encoding="utf-8") == ORIGINAL)
    page.wait_for_selector(_row("src/app.py"), state="detached", timeout=8000)
    assert page.locator("[data-pc='review']").is_hidden()
    assert not page.errors


def test_diff_modal_agent_mode_keep_hunk_toggle_and_undo_file(agent_turn, page, project):
    agent_turn({"src/app.py": AGENT})
    page.goto(ORIGIN + "/stub.html")
    page.wait_for_selector(_row("src/app.py") + " .pending-changes-review-badge", timeout=8000)
    page.click(_row("src/app.py") + " .pending-changes-item")
    page.wait_for_selector(".pending-diff-hunk.is-review", timeout=8000)
    assert page.locator(".pending-diff-hunk.is-review").count() == 2
    assert page.locator("[data-diff='viewToggle']").is_hidden()
    removed = page.locator(".pending-diff-hunk.is-review .pending-diff-line.is-del .pending-diff-txt").all_inner_texts()
    assert removed == ["    return 'hi'"]
    # Keep the header hunk: one left, disk untouched.
    page.locator(".pending-diff-hunk.is-review").nth(0).locator("[data-review-kind='keep']").click()
    page.wait_for_function("document.querySelectorAll('.pending-diff-hunk.is-review').length === 1", timeout=8000)
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == AGENT
    # Working tree is the plain git diff; Agent changes comes back.
    page.click("[data-diff-source='git']")
    page.wait_for_selector("[data-diff='viewToggle']:visible", timeout=8000)
    assert page.locator(".pending-diff-hunk.is-review").count() == 0
    page.click("[data-diff-source='agent']")
    page.wait_for_selector(".pending-diff-hunk.is-review", timeout=8000)
    # Undo file asks, reverts the remaining agent change, and falls back to git.
    page.click("[data-review-file='undo']")
    page.click(".cuttle-review-dialog button[value='ok']")
    expected = "# greeting helpers\ndef hello():\n    return 'hi'\n"
    assert _wait(page, lambda: (project / "src" / "app.py").read_text(encoding="utf-8") == expected)
    page.wait_for_selector("[data-diff='sourceToggle']", state="hidden", timeout=8000)
    assert not page.errors


def test_commit_guard_warns_cancels_then_keeps_and_remembers(agent_turn, page, project):
    from api.agent_review import service

    agent_turn({"src/app.py": AGENT})
    before = _commits(project)
    page.goto(ORIGIN + "/stub.html")
    page.wait_for_selector(_row("src/app.py") + " .pending-changes-review-badge", timeout=8000)
    page.click("[data-pc='commitBtn']")
    page.wait_for_function("document.querySelector('[data-pc=\"commitMessage\"]').value === 'Agent work'", timeout=8000)
    page.click("[data-pc='commitSubmit']")
    page.wait_for_selector(".cuttle-review-dialog", timeout=8000)
    assert "src/app.py" in page.inner_text(".cuttle-review-dialog")
    page.click(".cuttle-review-dialog button[value='']")
    page.wait_for_selector(".cuttle-review-dialog", state="detached")
    assert _commits(project) == before and service.list_reviews(project)["files"]
    page.click("[data-pc='commitSubmit']")
    page.check(".cuttle-review-dialog [name='remember']")
    page.click(".cuttle-review-dialog button[value='ok']")
    assert _wait(page, lambda: _commits(project) == before + 1, timeout=10)
    assert service.list_reviews(project)["files"] == []
    assert _wait(page, lambda: service.commit_policy() == "keep")
    assert not page.errors


def test_commit_guard_policy_keep_commits_without_asking(agent_turn, page, project):
    from api.agent_review import service

    service.set_commit_policy("keep")
    agent_turn({"src/app.py": AGENT})
    before = _commits(project)
    page.goto(ORIGIN + "/stub.html")
    page.wait_for_selector(_row("src/app.py") + " .pending-changes-review-badge", timeout=8000)
    page.click("[data-pc='commitBtn']")
    page.wait_for_function("document.querySelector('[data-pc=\"commitMessage\"]').value === 'Agent work'", timeout=8000)
    page.click("[data-pc='commitSubmit']")
    assert _wait(page, lambda: _commits(project) == before + 1, timeout=10)
    assert page.locator(".cuttle-review-dialog").count() == 0
    assert service.list_reviews(project)["files"] == []
    assert not page.errors
