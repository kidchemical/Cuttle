"""IDE App in a real browser against the real ``api.ide`` routes.

Pages and assets come from ``src/web``; ``/api/ide``, Git pending changes and
the experimental flags are answered in-process by the Flask test client over
a temporary git project. The chat side panel is a stub page (no agents run),
the terminal is not exercised (it needs a PTY WebSocket), and the Monaco CDN
is blocked so the offline plain-editor engine is what runs. Set
``CUTTLE_IDE_E2E_MONACO=1`` for a deliberate smoke run that loads Monaco from
jsDelivr instead.

Requires Playwright and Chromium (skipped otherwise).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.browser_guard import launch_chromium

WEB = Path(__file__).resolve().parents[2] / "web"
ORIGIN = "http://ide.test"
MONACO = os.environ.get("CUTTLE_IDE_E2E_MONACO") == "1"
FORWARD = ("/api/ide/", "/api/git/pending-changes", "/api/experimental/flags", "/ide_page.html", "/api/auth/sessions")

CHAT_STUB = """<!doctype html><html><body>
<textarea id="chatInput"></textarea><button id="sendButton">Send</button>
<script>
window.sent = [];
document.getElementById('sendButton').addEventListener('click', () => {
  const box = document.getElementById('chatInput');
  window.sent.push(box.value); box.value = '';
});
</script></body></html>"""


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


@pytest.fixture()
def project(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("git unavailable")
    root = tmp_path / "demo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\nhello world\n", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("artifact\n", encoding="utf-8")
    (root / ".gitignore").write_text("build/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    root = root.resolve()
    proj = {"id": 3, "name": "Demo", "path": str(root), "available": True}
    from api.ide import service
    from managers import project_manager as pm_mod

    monkeypatch.setattr(service, "_registered", lambda: ([proj], proj))

    class _PM:
        def get_projects(self, include_archived=False):
            return [proj]

        def get_current_project(self):
            return proj

    monkeypatch.setattr(pm_mod, "project_manager", _PM())
    import api.git_routes as git_routes
    monkeypatch.setattr(git_routes, "project_manager", _PM())
    return root


@pytest.fixture()
def flask_client(tmp_path, monkeypatch, project):
    from api.experimental import flags
    from tests.auth.test_http_authz import _auth_client

    monkeypatch.delenv("CUTTLE_EXPERIMENTAL", raising=False)
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    flags.set_enabled("ide", True)
    return ctx["client"]


@pytest.fixture(scope="module")
def browser():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as runtime:
        instance = launch_chromium(runtime)
        yield instance
        instance.close()


class Harness:
    def __init__(self, client, project):
        self.client = client
        self.project = project
        self.sessions = []

    def handle(self, route):
        req = route.request
        parsed = urlparse(req.url)
        if parsed.netloc != "ide.test":
            if MONACO and parsed.netloc == "cdn.jsdelivr.net" and "monaco-editor" in parsed.path:
                return route.continue_()
            return route.abort()
        path = parsed.path
        if path == "/api/auth/sessions" and req.method == "POST":
            self.sessions.append(json.loads(req.post_data or "{}"))
        if path.startswith(FORWARD):
            res = self.client.open(path, method=req.method, query_string=parsed.query,
                                   data=req.post_data_buffer, content_type=req.headers.get("content-type"))
            return route.fulfill(status=res.status_code, body=res.get_data(),
                                 headers={"content-type": res.headers.get("content-type", "application/json")})
        if path == "/chat_page.html":
            return route.fulfill(body=CHAT_STUB, content_type="text/html")
        if path.startswith("/api/"):
            return route.fulfill(json={"success": False, "error": "not in this test"}, status=404)
        target = (WEB / path.lstrip("/")).resolve()
        if not target.is_file() or not target.is_relative_to(WEB):
            return route.fulfill(status=404, body="missing")
        ctype = {".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}.get(target.suffix, "application/octet-stream")
        return route.fulfill(body=target.read_bytes(), content_type=ctype)


@pytest.fixture()
def page(browser, flask_client, project):
    harness = Harness(flask_client, project)
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    context.add_init_script("localStorage.clear()")
    pg = context.new_page()
    pg.route("**/*", harness.handle)
    errors = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    pg.harness = harness
    pg.errors = errors
    pg.goto(ORIGIN + "/ide_page.html")
    pg.wait_for_selector(".ide-tree-row[data-path='README.md']", timeout=15000)
    yield pg
    context.close()


def _editor_text(page):
    if MONACO:
        return page.evaluate("monaco.editor.getModels().find(m => m.uri.path === '/' + document.querySelector('.ide-tab.is-active').dataset.path).getValue()")
    return page.input_value(".ide-textarea")


def _type(page, text):
    if MONACO:
        page.click(".monaco-editor .view-lines")
        page.keyboard.press("Control+End")
    else:
        page.click(".ide-textarea")
        page.keyboard.press("Control+End")
    page.keyboard.type(text)


def test_explorer_open_edit_save(page, project):
    assert page.locator(".ide-tree-row[data-path='build']").get_attribute("class").count("is-ignored") == 1
    assert page.locator(".ide-tree-row[data-path='src']").is_visible()
    engine = "monaco" if MONACO else "textarea"
    page.click(".ide-tree-row[data-path='README.md']")
    page.wait_for_selector(".ide-tab.is-preview[data-path='README.md']")
    page.wait_for_function("document.querySelector('.ide-crumb.is-file')?.textContent === 'README.md'")
    assert "hello world" in _editor_text(page)
    if engine == "monaco":
        page.wait_for_selector(".monaco-editor")
    _type(page, "more\n")
    page.wait_for_selector(".ide-tab.is-dirty[data-path='README.md']:not(.is-preview)")
    page.keyboard.press("Control+s")
    page.wait_for_selector(".ide-tab[data-path='README.md']:not(.is-dirty)")
    assert (project / "README.md").read_text(encoding="utf-8") == "# Demo\nhello world\nmore\n"
    # Git decoration follows the save.
    page.wait_for_selector(".ide-tree-row.git-m[data-path='README.md']", timeout=8000)
    assert not page.errors


def test_external_change_is_followed_and_can_be_undone(page, project):
    page.dblclick(".ide-tree-row[data-path='README.md']")
    page.wait_for_selector(".ide-tab[data-path='README.md']")
    (project / "README.md").write_text("# Demo\nhello agent\n", encoding="utf-8")
    page.wait_for_selector("[data-banner='review']", timeout=8000)
    assert "hello agent" in _editor_text(page)
    page.click("[data-banner='review']")
    page.wait_for_selector("#ideDiffView:not([hidden])")
    page.click("[data-banner='undo']")
    page.wait_for_function("document.getElementById('ideBanner').hidden")
    deadline = time.time() + 5
    while time.time() < deadline and "hello world" not in (project / "README.md").read_text(encoding="utf-8"):
        time.sleep(0.1)
    assert (project / "README.md").read_text(encoding="utf-8") == "# Demo\nhello world\n"


def test_conflict_with_unsaved_edits(page, project):
    page.dblclick(".ide-tree-row[data-path='README.md']")
    page.wait_for_selector(".ide-tab[data-path='README.md']")
    _type(page, "mine\n")
    page.wait_for_selector(".ide-tab.is-dirty")
    (project / "README.md").write_text("theirs\n", encoding="utf-8")
    page.wait_for_selector("[data-banner='overwrite']", timeout=8000)
    assert "mine" in _editor_text(page)
    page.click("[data-banner='overwrite']")
    page.wait_for_selector(".ide-tab[data-path='README.md']:not(.is-dirty)")
    assert (project / "README.md").read_text(encoding="utf-8").endswith("mine\n")


def test_quick_open_search_and_new_file(page, project):
    page.keyboard.press("Control+p")
    page.wait_for_selector("#ideQuick:not([hidden])")
    page.keyboard.type("app")
    page.wait_for_selector(".ide-quick-item .ide-quick-desc:text('src')")
    page.keyboard.press("Enter")
    page.wait_for_selector(".ide-tab.is-active[data-path='src/app.py']")
    # Command palette via '>' prefix.
    page.keyboard.press("Control+Shift+p")
    page.keyboard.type("toggle word")
    page.wait_for_selector(".ide-quick-item:has-text('Toggle Word Wrap')")
    page.keyboard.press("Escape")
    page.keyboard.press("Control+Shift+f")
    page.wait_for_selector("[data-view-panel='search']:not([hidden])")
    page.fill("[data-s='query']", "hello")
    page.wait_for_selector(".ide-search-match", timeout=8000)
    assert "2 files" in page.inner_text("[data-s='summary']")
    page.click(".ide-search-file[data-file='README.md'] + .ide-search-match")
    page.wait_for_selector(".ide-tab.is-active[data-path='README.md']")
    page.click("[data-view='explorer']")
    page.click("#ideSidebar [data-cmd='file.new']")
    page.wait_for_selector(".ide-quick-input")
    page.keyboard.type("docs/notes.md")
    page.keyboard.press("Enter")
    page.wait_for_selector(".ide-tab.is-active[data-path='docs/notes.md']")
    assert (project / "docs" / "notes.md").is_file()
    page.wait_for_selector(".ide-tree-row[data-path='docs/notes.md']")


def test_ai_chat_context_and_inline_edit(page, project):
    page.dblclick(".ide-tree-row[data-path='README.md']")
    page.wait_for_function("document.querySelector('.ide-crumb.is-file')?.textContent === 'README.md'")
    page.click(".monaco-editor .view-lines" if MONACO else ".ide-textarea")
    page.keyboard.press("Control+Home")
    page.keyboard.press("Shift+End")
    page.keyboard.press("Control+l")
    frame = page.wait_for_selector("iframe.ide-chat-frame").content_frame()
    frame.wait_for_function("document.getElementById('chatInput').value.includes('README.md')")
    assert "```markdown" in frame.eval_on_selector("#chatInput", "el => el.value")
    assert page.harness.sessions and page.harness.sessions[0]["session_name"] == "IDE · Demo"
    # The real auth routes created the chat and bound it to the project.
    from api.auth_db import get_auth_db
    sid = int(frame.url.split("chat=")[1])
    assert get_auth_db().get_session_project(sid)["project_path"] == str(project)
    # Ctrl+L focused the chat composer; inline edit starts from the editor.
    page.click(".monaco-editor .view-lines" if MONACO else ".ide-textarea")
    page.keyboard.press("Control+k")
    page.wait_for_selector("#ideInlineEdit:not([hidden])")
    page.fill("#ideInlineEditText", "Make the heading friendlier")
    page.keyboard.press("Enter")
    frame.wait_for_function("window.sent.length === 1")
    sent = frame.evaluate("window.sent[0]")
    assert "Make the heading friendlier" in sent and "README.md" in sent
    page.wait_for_selector("[data-banner='dismissAgent']")
    # The agent's edit lands on disk → review banner.
    (project / "README.md").write_text("# Hello, Demo!\nhello world\n", encoding="utf-8")
    page.wait_for_selector("[data-banner='keep']", timeout=8000)
    page.click("[data-banner='keep']")
    page.wait_for_function("document.getElementById('ideBanner').hidden")


def test_disabled_flag_shows_notice(browser, flask_client, project):
    from api.experimental import flags

    harness = Harness(flask_client, project)
    context = browser.new_context()
    pg = context.new_page()
    pg.route("**/*", harness.handle)
    # The page route itself 403s while disabled; serve it statically to cover
    # the in-page guard (flag turned off after the page loaded).
    pg.route(ORIGIN + "/ide_page.html", lambda r: r.fulfill(body=(WEB / "ide_page.html").read_bytes(), content_type="text/html"))
    flags.set_enabled("ide", False)
    pg.goto(ORIGIN + "/ide_page.html")
    pg.wait_for_selector("#ideNotice:not([hidden])")
    assert "turned off" in pg.inner_text("#ideNotice")
    assert pg.locator("#ideApp").is_hidden()
    context.close()


DIFF_STUB = """<!doctype html><html><head><link rel="stylesheet" href="/css/diff_modal.css"></head><body>
<script src="/js/git/diff_modal.js"></script></body></html>"""


@pytest.mark.parametrize("flag_on", [True, False])
def test_diff_modal_editor_button(browser, flag_on):
    context = browser.new_context()
    pg = context.new_page()
    navigations = []

    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/stub.html":
            return route.fulfill(body=DIFF_STUB, content_type="text/html")
        if path == "/api/experimental/flags":
            return route.fulfill(json={"success": True, "flags": [{"id": "ide", "enabled": flag_on}]})
        if path == "/api/git/pending-diff":
            file = dict(pair.split("=", 1) for pair in urlparse(route.request.url).query.split("&"))["file"]
            data = {"success": True, "path": file, "status": "modified", "hunks": [{"header": "@@", "lines": [{"type": "add", "text": "x", "new_no": 1}]}]}
            if "bin" in file:
                data.update(binary=True, hunks=[])
            if "gone" in file:
                data.update(status="deleted")
            return route.fulfill(json=data)
        if path == "/ide_page.html":
            navigations.append(route.request.url)
            return route.fulfill(body="<p>ide</p>", content_type="text/html")
        target = WEB / path.lstrip("/")
        return route.fulfill(body=target.read_bytes(), content_type="application/javascript" if path.endswith(".js") else "text/css")

    pg.route("**/*", handle)
    pg.goto(ORIGIN + "/stub.html")
    pg.evaluate("CuttleDiffModal.open({projectPath: '/p/demo', repoRoot: '/p/demo', file: 'src/a.py', files: ['src/a.py', 'logo.bin', 'gone.txt']})")
    pg.wait_for_selector(".pending-diff-line")
    pg.wait_for_timeout(200)
    editor = pg.locator("[data-diff='editor']")
    assert editor.is_visible() is flag_on
    if flag_on:
        pg.keyboard.press("ArrowRight")
        pg.wait_for_selector("[data-diff='empty']:not([hidden])")
        pg.wait_for_timeout(200)
        assert not editor.is_visible(), "binary files have no Editor button"
        pg.keyboard.press("ArrowRight")
        pg.wait_for_timeout(300)
        assert not editor.is_visible(), "deleted files have no Editor button"
        pg.keyboard.press("ArrowLeft")
        pg.keyboard.press("ArrowLeft")
        pg.wait_for_selector("[data-diff='editor']:visible")
        editor.click()
        pg.wait_for_url("**/ide_page.html?**")
        assert "file=src%2Fa.py" in navigations[0] and "path=%2Fp%2Fdemo" in navigations[0]
    context.close()
