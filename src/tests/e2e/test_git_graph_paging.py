"""Git page commit list pages in older history (scroll, tall viewport, footer).

Isolated: static web root + a fake paginated /api/git/graph; no Flask or Git.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from .test_shared_diff_modal import apply_request_guard, browser, static_server  # noqa: F401

PROJECT = {"id": 1, "name": "Repo", "path": "/repo/Repo", "type": "local"}
TOTAL = 150


def _history(total):
    commits = []
    for i in range(total):
        h = f"{i:040x}"
        parent = f"{i + 1:040x}" if i + 1 < total else None
        commits.append({
            "hash": h, "short": h[:7], "parents": [parent] if parent else [],
            "author": "Owner", "email": "o@example.com",
            "date": "2026-10-01T12:00:00+00:00",
            "subject": "release notes" if i == 140 else f"commit {i}",
            "refs": ["HEAD", "main", "v1.0"] if i == 0 else [],
            "tags": ["v1.0"] if i == 0 else [],
        })
    return commits


class GraphAPI:
    def __init__(self):
        self.commits = _history(TOTAL)
        self.skips = []
        self.fail_skips = set()

    def handle(self, route):
        parsed = urlparse(route.request.url)
        query = parse_qs(parsed.query)
        data = {"success": True, "data": []}
        if parsed.path == "/api/projects":
            data = {"success": True, "data": [PROJECT]}
        elif parsed.path == "/api/git/repos":
            data = {"success": True, "repos": [{"repo_root": PROJECT["path"], "branch": "main"}]}
        elif parsed.path == "/api/git/pending-changes":
            data = {"success": True, "project": PROJECT, "repos": [
                {"repo_root": PROJECT["path"], "files": [], "clean": True, "branch": "main", "total_files": 0}]}
        elif parsed.path == "/api/git/graph":
            skip = int(query.get("skip", ["0"])[0])
            limit = int(query.get("limit", ["80"])[0])
            self.skips.append(skip)
            if skip in self.fail_skips:
                data = {"success": False, "error": "git log timed out"}
            else:
                page = self.commits[skip:skip + limit]
                data = {"success": True, "commits": page, "branch": "main",
                        "branches": [{"name": "main", "current": True}],
                        "has_more": skip + limit < len(self.commits)}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))


@pytest.fixture
def git_page(browser, static_server):
    opened = []

    def load(height=800, api=None):
        api = api or GraphAPI()
        page = browser.new_page(viewport={"width": 1280, "height": height})
        apply_request_guard(page.context, static_server)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route(f"{static_server}/api/**", api.handle)
        page.goto(f"{static_server}/git_graph_page.html", wait_until="domcontentloaded")
        page.locator(".git-graph-row").first.wait_for()
        opened.append(page)
        return page, api, errors

    yield load
    for page in opened:
        page.close()


def _scroll_to_end(page):
    page.evaluate("() => { const s = document.getElementById('gitGraphScroll'); s.scrollTop = s.scrollHeight; }")


def test_tall_viewport_fills_past_first_page_without_scrolling(git_page):
    # The first page used to fit without a scrollbar, so no scroll event ever
    # asked for more and history stopped at the first page.
    page, api, errors = git_page(height=3200)
    page.wait_for_function("() => document.getElementById('gitGraphSubtitle').textContent.startsWith('120')")
    assert api.skips[:2] == [0, 60]
    assert errors == []


def test_scrolling_reaches_start_of_history(git_page):
    page, api, errors = git_page()
    for _ in range(6):
        _scroll_to_end(page)
        page.wait_for_timeout(150)
    more = page.locator("#gitGraphMore")
    page.wait_for_function("() => document.getElementById('gitGraphMore').textContent.includes('Start of history')")
    assert more.inner_text() == f"Start of history · {TOTAL} commits"
    assert sorted(set(api.skips)) == [0, 60, 120]
    assert page.locator(".git-graph-row").last.locator(".git-graph-subject").inner_text() == f"commit {TOTAL - 1}"
    assert errors == []


def test_footer_button_loads_more_and_filter_pauses_auto_load(git_page):
    page, api, errors = git_page()
    page.fill("#gitGraphFilter", "release")
    more = page.locator("#gitGraphMore")
    page.wait_for_function("() => document.getElementById('gitGraphMore').textContent.includes('0 of 60')")
    page.wait_for_timeout(300)
    assert api.skips == [0]  # filtering never walks history on its own
    more.get_by_role("button", name="Search older commits").click()
    page.wait_for_function("() => document.getElementById('gitGraphMore').textContent.includes('of 120')")
    more.get_by_role("button", name="Search older commits").click()
    page.wait_for_function("() => document.getElementById('gitGraphMore').textContent.includes('1 of 150')")
    assert page.locator(".git-graph-subject").all_inner_texts() == ["release notes"]
    assert "start of history" in more.inner_text()
    assert errors == []


def test_failed_page_offers_retry_instead_of_looping(git_page):
    api = GraphAPI()
    api.fail_skips = {60}
    page, api, errors = git_page(api=api)
    _scroll_to_end(page)
    retry = page.locator("#gitGraphMore").get_by_role("button", name="Retry")
    retry.wait_for()
    _scroll_to_end(page)
    page.wait_for_timeout(300)
    assert api.skips.count(60) == 1
    api.fail_skips.clear()
    retry.click()
    page.wait_for_function("() => document.getElementById('gitGraphSubtitle').textContent.startsWith('120')")
    assert errors == []


def test_ref_chips_by_kind(git_page):
    page, _api, errors = git_page()
    chips = page.locator(".git-graph-row").first.locator(".git-graph-ref")
    assert chips.all_inner_texts() == ["main", "v1.0"]  # HEAD folds into the branch chip
    assert "is-head" in chips.nth(0).get_attribute("class")
    assert "is-tag" in chips.nth(1).get_attribute("class")
    assert errors == []
