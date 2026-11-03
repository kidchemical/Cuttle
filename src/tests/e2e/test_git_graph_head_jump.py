"""Git page: jump HEAD between commits from the commit drawer.

Isolated: static web root + a fake /api/git/graph + /api/git/checkout; no Flask or Git.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

import pytest

from .test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401

PROJECT = {"id": 1, "name": "Repo", "path": "/repo/Repo", "type": "local"}
HASHES = [f"{i:x}" * 40 for i in range(10, 14)]  # newest first: aaaa…, bbbb…, …


class RepoAPI:
    def __init__(self):
        self.head = HASHES[0]
        self.branch = "main"
        self.checkouts = []

    def _commit(self, i, h):
        refs = []
        if h == self.head:
            refs.append("HEAD")
        if i == 0:
            refs.append("main")
        return {"hash": h, "short": h[:7], "parents": [HASHES[i + 1]] if i + 1 < len(HASHES) else [],
                "author": "Owner", "email": "o@example.com", "date": "2026-10-01T12:00:00+00:00",
                "subject": f"commit {i}", "refs": refs, "tags": []}

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path
        data = {"success": True, "data": []}
        commits = [self._commit(i, h) for i, h in enumerate(HASHES)]
        if path == "/api/projects":
            data = {"success": True, "data": [PROJECT]}
        elif path == "/api/git/repos":
            data = {"success": True, "repos": [{"repo_root": PROJECT["path"], "branch": "main"}]}
        elif path == "/api/git/pending-changes":
            data = {"success": True, "project": PROJECT, "repos": [
                {"repo_root": PROJECT["path"], "files": [], "clean": True, "branch": "main", "total_files": 0}]}
        elif path == "/api/git/graph":
            data = {"success": True, "commits": commits, "branch": self.branch or "HEAD",
                    "head": self.head, "detached": not self.branch,
                    "branches": [{"name": "main", "current": bool(self.branch)}], "has_more": False}
        elif path.startswith("/api/git/commit/") and path.endswith("/detail"):
            h = path.split("/")[4]
            c = next(c for c in commits if c["hash"] == h)
            data = {"success": True, **c, "files": [], "stat": "", "body": ""}
        elif path == "/api/git/checkout":
            ref = json.loads(req.post_data)["ref"]
            self.checkouts.append(ref)
            if ref == "main":
                self.head, self.branch = HASHES[0], "main"
            else:
                self.head, self.branch = ref, None
            data = {"success": True, "head": self.head, "branch": self.branch,
                    "detached": not self.branch, "message": "moved"}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))


@pytest.fixture
def page_api(browser, static_server):
    api = RepoAPI()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    apply_request_guard(page.context, static_server)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route(f"{static_server}/api/**", api.handle)
    page.goto(f"{static_server}/git_graph_page.html", wait_until="domcontentloaded")
    page.locator(".git-graph-row").first.wait_for()
    yield page, api, errors
    page.close()


def _open(page, i):
    page.locator(f'.git-graph-row[data-hash="{HASHES[i]}"]').click()
    page.locator(".git-graph-head-actions").wait_for()
    return page.locator(".git-graph-head-actions")


def test_click_commit_then_checkout_and_return(page_api):
    page, api, errors = page_api
    bar = _open(page, 0)
    assert bar.inner_text().strip() == "HEAD"
    assert bar.locator("button").count() == 0

    bar = _open(page, 2)
    btn = bar.get_by_role("button", name="Check out")
    btn.click()
    assert api.checkouts == []  # first click only arms
    bar.get_by_role("button", name="Confirm?").click()
    page.wait_for_function(
        "() => document.getElementById('gitGraphSubtitle').textContent.includes('HEAD detached at')")
    assert api.checkouts == [HASHES[2]]
    bar = page.locator(".git-graph-head-actions")
    bar.get_by_text("HEAD · detached").wait_for()

    # Another commit while detached offers a way back to the branch.
    bar = _open(page, 3)
    back = bar.get_by_role("button", name="Back to main")
    back.click()
    bar.get_by_role("button", name="Confirm?").click()
    page.wait_for_function(
        "() => !document.getElementById('gitGraphSubtitle').textContent.includes('detached')")
    assert api.checkouts == [HASHES[2], "main"]
    assert errors == []
