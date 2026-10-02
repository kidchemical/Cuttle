"""Exercise the actual chat/Git pending-change entry points with an isolated API.

No Flask or desktop applications are launched. Requires Playwright and Chromium;
CUTTLE_TEST_CHROMIUM can select an existing browser executable.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
PROJECT = {"id": 1, "name": "Cuttle", "path": "/repo/Cuttle", "type": "local"}
ROOT = "/repo/Cuttle/nested-repo"
FILES = [
    {"path": "src/web/chat_page.html", "status": "modified", "additions": 1, "deletions": 1},
    {"path": 'scripts/helper with "quotes".py', "status": "added", "additions": 1, "deletions": 0},
]
COMMIT = {"hash": "abcdef1234567890", "short": "abcdef1", "subject": "Example change", "author": "Owner", "date": "2026-10-01", "parents": [], "refs": ["HEAD"]}


@pytest.fixture(scope="module")
def static_server():
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(WEB))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    executable = os.environ.get("CUTTLE_TEST_CHROMIUM")
    if not executable and Path("/opt/google/chrome/chrome").exists():
        executable = "/opt/google/chrome/chrome"
    with pw.sync_playwright() as runtime:
        try:
            instance = runtime.chromium.launch(headless=True, executable_path=executable)
        except pw.Error as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        yield instance
        instance.close()


class IsolatedAPI:
    def __init__(self):
        self.requests = []
        self.truncated = False

    def handle(self, route):
        parsed = urlparse(route.request.url)
        query = parse_qs(parsed.query)
        path = parsed.path
        self.requests.append((path, query, route.request.post_data))
        repo = {"repo_root": ROOT, "files": FILES, "clean": False, "branch": "main", "total_files": 2}
        data = {"success": True, "data": []}
        if path == "/api/projects":
            data = {"success": True, "data": [PROJECT]}
        elif path == "/api/auth/me":
            data = {"authenticated": True, "user": {"id": 1, "display_name": "Owner", "role": "owner"}}
        elif path == "/api/auth/sessions":
            data = {"success": True, "sessions": []}
        elif path.endswith("/messages"):
            data = {"success": True, "messages": []}
        elif path == "/api/git/pending-changes":
            data = {"success": True, "repos": [repo], "project": PROJECT}
        elif path == "/api/git/repos":
            data = {"success": True, "repos": [repo]}
        elif path == "/api/git/graph":
            data = {"success": True, "commits": [COMMIT], "branches": [], "branch": "main"}
        elif path.endswith("/detail"):
            data = {"success": True, **COMMIT, "files": FILES}
        elif path == "/api/git/pending-diff" or path.endswith("/file-diff"):
            lines = [
                {"type": "del", "text": "old <script>literal</script>", "old_no": 1},
                {"type": "add", "text": "new text", "new_no": 1},
            ]
            truncated = self.truncated and query.get("full") != ["1"]
            hunks = [{"header": "@@ -1 +1 @@", "lines": lines}]
            section = lambda line: {"shown": 1, "total": 901 if truncated else 1, "truncated": truncated, "hunks": [{"header": "@@ -1 +1 @@", "lines": [line]}]}
            data = {"success": True, "path": query["file"][0], "status": "modified", "additions": 1, "deletions": 1,
                    "hunks": hunks, "sections": {"added": section(lines[1]), "removed": section(lines[0])},
                    "truncated": truncated, "full": query.get("full") == ["1"], "max_lines_per_side": int(query.get("max_lines", ["300"])[0])}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data))


def apply_request_guard(context, allowed_origin):
    """Share one network allowlist across the isolated browser suites.

    Register on the page's context BEFORE any page-level route: requests
    to the static fixture server fall through to the fake same-origin API
    handlers, while every other HTTP(S) destination — CDNs, fonts, sibling
    localhost services such as a live Flask on :8080 — aborts. Returns the
    list of aborted URLs so tests can assert the guard fired.
    """
    blocked = []
    prefix = allowed_origin.rstrip("/")

    def guard(route):
        url = route.request.url
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https"):
            route.fallback()
            return
        if url == prefix or url.startswith(prefix + "/"):
            route.fallback()
            return
        blocked.append(url)
        route.abort()

    context.route("**/*", guard)
    return blocked


@pytest.fixture
def surface(browser, static_server):
    opened = []

    def load(name):
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        apply_request_guard(page.context, static_server)
        api = IsolatedAPI()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route(f"{static_server}/api/**", api.handle)
        page.goto(f"{static_server}/{name}", wait_until="domcontentloaded")
        if name == "chat_page.html":
            page.wait_for_function("typeof window.loadChatSession === 'function'")
            page.evaluate("() => window.loadChatSession(42)")
        opened.append(page)
        return page, api, errors

    yield load
    for page in opened:
        page.close()


def test_request_guard_blocks_external_allows_same_origin(browser, static_server):
    """The shared guard aborts CDN/live-localhost traffic, keeps fixtures working."""
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    try:
        blocked = apply_request_guard(context, static_server)
        page = context.new_page()
        page.goto(f"{static_server}/chat_page.html", wait_until="domcontentloaded")
        assert page.evaluate("() => document.title.length > 0")
        same_origin = page.evaluate(
            "async () => { const r = await fetch('/chat_page.html'); return r.status; }"
        )
        assert same_origin == 200
        page.route(
            f"{static_server}/api/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"ok": true}',
            ),
        )
        ping = page.evaluate(
            "async () => { const r = await fetch('/api/ping');"
            " return [r.status, await r.json()]; }"
        )
        assert ping == [200, {"ok": True}]
        for url in (
            "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
            "https://fonts.googleapis.com/css2?family=Inter",
            "http://127.0.0.1:8080/api/health",
        ):
            outcome = page.evaluate(
                "async (u) => { try { await fetch(u); return 'loaded'; }"
                " catch (e) { return 'blocked'; } }",
                url,
            )
            assert outcome == "blocked", url
        assert any("cdn.jsdelivr.net" in u for u in blocked)
        assert any("fonts.googleapis.com" in u for u in blocked)
        assert any("127.0.0.1:8080" in u for u in blocked)
        assert not any(u.startswith(static_server) for u in blocked)
    finally:
        context.close()


def open_pending(page):
    page.locator('[data-pc="toggle"]').first.click()
    page.locator(".pending-changes-item").first.click()
    page.locator('#cuttleDiffModal [data-diff="hunks"]').wait_for(state="visible")


@pytest.mark.parametrize("name", ["chat_page.html", "git_graph_page.html"])
def test_pending_popup_title_views_navigation_and_focus(surface, name):
    page, api, errors = surface(name)
    open_pending(page)
    popup = page.locator("#cuttleDiffModal")
    title = popup.locator('[data-diff="title"]')
    assert title.evaluate("el => el.tagName") == "A"
    assert title.inner_text() == FILES[0]["path"]
    assert popup.evaluate("el => el.matches(':modal')")
    assert popup.locator(".pending-diff-side").count() == 2
    assert popup.locator("script").count() == 0  # diff text is escaped
    assert popup.locator(".pending-diff-line").first.evaluate("el => getComputedStyle(el).fontSize") == "11px"
    title.click()
    posts = [json.loads(body) for path, _query, body in api.requests if path == "/api/git/open-file"]
    assert posts[-1] == {"path": PROJECT["path"], "repo_root": ROOT, "file": FILES[0]["path"]}
    popup.locator('[data-diff-view="unified"]').click()
    assert popup.locator(".pending-diff-side").count() == 0
    assert popup.locator(".pending-diff-line").first.get_attribute("class").endswith("is-del")
    popup.locator('[data-diff="next"]').click()
    page.wait_for_function("document.querySelector('[data-diff=title]').textContent === " + json.dumps(FILES[1]["path"]))
    popup.locator('[data-diff="hunks"]').wait_for(state="visible")
    assert popup.locator('[data-diff="next"]').is_disabled()
    assert popup.locator('[data-diff="position"]').inner_text() == "2 / 2"
    title.click()
    posts = [json.loads(body) for path, _query, body in api.requests if path == "/api/git/open-file"]
    assert posts[-1]["file"] == FILES[1]["path"]
    assert posts[-1]["repo_root"] == ROOT
    page.keyboard.press("ArrowLeft")
    page.wait_for_function("document.querySelector('[data-diff=title]').textContent === " + json.dumps(FILES[0]["path"]))
    page.keyboard.press("Escape")
    assert not popup.is_visible()
    assert page.locator(".pending-changes-item").first.evaluate("el => document.activeElement === el")
    page.locator(".pending-changes-item").first.click()
    assert popup.locator('[data-diff-view="unified"]').get_attribute("aria-pressed") == "true"
    assert page.locator("#cuttleDiffModal").count() == 1
    assert errors == []


@pytest.mark.parametrize("name", ["chat_page.html", "git_graph_page.html"])
def test_expanding_diff_preserves_worktree(surface, name):
    page, api, errors = surface(name)
    api.truncated = True
    open_pending(page)
    page.locator(".pending-diff-expand-btn", has_text="Show more").first.click()
    page.wait_for_function("document.querySelector('[data-diff=hunks]').hidden === false")
    pending = [(path, query) for path, query, _body in api.requests if path == "/api/git/pending-diff"]
    assert pending[-1][1]["max_lines"] == ["900"]
    assert pending[-1][1]["repo_root"] == [ROOT]
    page.locator(".pending-diff-expand-btn", has_text="Show all").first.click()
    page.wait_for_function("document.querySelector('[data-diff=hunks]').hidden === false")
    pending = [(path, query) for path, query, _body in api.requests if path == "/api/git/pending-diff"]
    assert pending[-1][1]["full"] == ["1"]
    assert pending[-1][1]["repo_root"] == [ROOT]
    assert page.locator(".pending-diff-expand-btn").count() == 0
    assert errors == []


def test_commit_detail_uses_same_popup_and_retains_drawer_on_escape(surface):
    page, api, errors = surface("git_graph_page.html")
    page.locator(".git-graph-row").first.click()
    page.locator(".git-graph-file-btn").first.click()
    popup = page.locator("#cuttleDiffModal")
    popup.locator('[data-diff="hunks"]').wait_for(state="visible")
    assert popup.locator('[data-diff="source"]').inner_text() == COMMIT["short"]
    commit_requests = [(path, query) for path, query, _body in api.requests if path.endswith("/file-diff")]
    assert commit_requests[-1][0] == f'/api/git/commit/{COMMIT["hash"]}/file-diff'
    assert commit_requests[-1][1]["repo_root"] == [ROOT]
    page.keyboard.press("Escape")
    assert not popup.is_visible()
    assert page.locator("#gitGraphDrawer").is_visible()
    open_pending(page)
    assert page.locator("#cuttleDiffModal").count() == 1
    assert popup.locator('[data-diff="source"]').inner_text() == ""
    assert errors == []


def test_newer_file_request_wins_and_close_discards_delayed_results(surface):
    page, _api, errors = surface("git_graph_page.html")
    page.evaluate("""() => {
        const original = window.fetch;
        window.delayedDiffs = [];
        window.fetch = (url, options) => {
            if (String(url).includes('/api/git/pending-diff?')) {
                return new Promise(resolve => delayedDiffs.push(resolve));
            }
            return original(url, options);
        };
        CuttleDiffModal.open({projectPath: '/repo/Cuttle', repoRoot: '/repo/Cuttle/nested-repo', file: 'first.py', files: ['first.py', 'second.py']});
        CuttleDiffModal.navigate(1);
        const response = text => new Response(JSON.stringify({success:true, hunks:[], message:text}));
        delayedDiffs[1](response('second result'));
        delayedDiffs[0](response('stale first result'));
    }""")
    page.wait_for_function("document.querySelector('[data-diff=empty]').textContent === 'second result'")
    assert page.locator('[data-diff="title"]').inner_text() == "second.py"
    page.evaluate("""() => {
        CuttleDiffModal.navigate(-1);
        CuttleDiffModal.close();
        delayedDiffs[2](new Response(JSON.stringify({success:true,hunks:[],message:'late result'})));
    }""")
    assert not page.locator("#cuttleDiffModal").is_visible()
    assert errors == []


@pytest.mark.parametrize("name", ["chat_page.html", "git_graph_page.html"])
def test_compact_light_popup_keeps_controls_visible(surface, name):
    page, _api, errors = surface(name)
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("document.documentElement.classList.add('light-mode'); document.body.classList.add('light-mode')")
    open_pending(page)
    popup = page.locator("#cuttleDiffModal")
    box = popup.locator(".pending-diff-modal-card").bounding_box()
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= 390
    assert popup.locator('[data-diff="title"]').is_visible()
    assert popup.locator('[data-diff="close"]').is_visible()
    assert not popup.locator('[data-diff="sideNext"]').is_visible()
    assert popup.locator(".is-add").evaluate("el => getComputedStyle(el).color") == "rgb(17, 99, 41)"
    assert errors == []


def test_chat_commit_diff_opens_above_detail_and_returns_focus(surface):
    page, _api, errors = surface("chat_page.html")
    page.evaluate("""hash => CuttleGitCommitViewer.openFromChat({
        hash, getProjectPath: () => '/repo/Cuttle', getRepoRoot: () => '/repo/Cuttle/nested-repo'
    })""", COMMIT["hash"])
    detail = page.locator("#gitCommitModal")
    detail.locator(".git-graph-file-btn").first.click()
    popup = page.locator("#cuttleDiffModal")
    popup.locator('[data-diff="hunks"]').wait_for(state="visible")
    assert popup.evaluate("el => el.matches(':modal')")
    page.keyboard.press("Escape")
    assert detail.is_visible()
    assert detail.locator(".git-graph-file-btn").first.evaluate("el => document.activeElement === el")
    # Reopening synchronously must survive the old dialog's queued close event.
    page.evaluate("""() => {
        const opts = {projectPath:'/repo/Cuttle', file:'first.py', files:['first.py']};
        CuttleDiffModal.open(opts);
        CuttleDiffModal.close();
        CuttleDiffModal.open(opts);
    }""")
    popup.locator('[data-diff="hunks"]').wait_for(state="visible")
    assert popup.is_visible()
    assert errors == []
