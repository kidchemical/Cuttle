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
import re
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
window.appearance = [];
window.addEventListener('message', e => window.appearance.push(e.data));
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


@pytest.mark.parametrize("theme", ["dark", "midnight", "light", "sakura", "mint",
                                  "lavender", "paper", "graphite", "nord", "rgb",
                                  "synthwave", "glitch", "matrix"])
def test_workspace_palette_and_wallpaper(page, theme):
    page.add_style_tag(content='*, *::before, *::after { transition: none !important; animation: none !important; }')
    page.click(".ide-tree-row[data-path='README.md']")
    page.wait_for_selector(".ide-tab.is-active")
    page.wait_for_function("""monacoEngine => monacoEngine
        ? monaco.editor.getModels().some(m => m.uri.path === '/README.md' && m.getValue().startsWith('# Demo'))
        : document.querySelector('.ide-textarea')?.value.startsWith('# Demo')""", arg=MONACO)
    page.evaluate("theme => CuttleUiBoot.applyTheme(theme)", theme)
    # Exercise the same live messages used by the shell; never reload the file.
    for video in (False, True, False):
        page.evaluate("active => CuttleUiBoot.setWallpaperActive(active)", video)
        appearance = page.evaluate("""() => {
            const alpha = selector => {
                const ctx = document.createElement('canvas').getContext('2d');
                ctx.fillStyle = getComputedStyle(document.querySelector(selector)).backgroundColor;
                ctx.fillRect(0, 0, 1, 1);
                return ctx.getImageData(0, 0, 1, 1).data[3];
            };
            const p = CuttleIdeTheme.palette();
            return {p, terminal: CuttleIdeTheme.terminalTheme(),
                monaco: CuttleIdeTheme.monacoTheme(),
                canvas: alpha('.ide-center'), sidebar: alpha('.ide-sidebar'),
                app: alpha('.ide-app'), body: alpha('body'),
                blur: getComputedStyle(document.querySelector('.ide-center')).backdropFilter,
                status: alpha('.ide-status'),
                overflow: document.documentElement.scrollWidth > innerWidth};
        }""")
        p = appearance["p"]
        assert appearance["terminal"]["foreground"] == p["text"]
        assert appearance["terminal"]["cursor"] == p["accent"]
        assert appearance["terminal"]["background"] == "#00000000"
        assert appearance["monaco"]["colors"]["editor.background"] == "#00000000"
        assert appearance["monaco"]["colors"]["editor.foreground"] == p["text"]
        assert appearance["monaco"]["base"] == ("vs" if p["light"] else "vs-dark")
        assert 150 < appearance["canvas"] < (230 if video else 255)
        assert 150 < appearance["sidebar"] < 255
        assert appearance["app"] == 0
        assert 150 < appearance["status"] < 255
        assert "blur" in appearance["blur"]
        assert not appearance["overflow"]
        if video:
            assert appearance["body"] == 0
        if theme in ("dark", "light"):
            assert p["accent"] == "#8b5cf6"
            assert p["text"] == ("#1a202c" if theme == "light" else "#e2e8f0")
        if video and theme in ("dark", "light", "sakura", "matrix"):
            page.screenshot(path=f"temp/ide-theme-{theme}.png")
    assert _editor_text(page) == "# Demo\nhello world\n"
    assert not page.errors


def test_shell_appearance_reaches_nested_chat(page):
    page.route(ORIGIN + "/ide-shell.html", lambda route: route.fulfill(
        body='''<html><body style="margin:0;background:#0f0c29">
        <video id="wallpaper" autoplay muted playsinline style="position:fixed;inset:0;width:100%;height:100%;object-fit:cover"></video>
        <iframe src="/ide_page.html" style="position:absolute;inset:0;width:100%;height:100%;border:0"></iframe>
        </body></html>''',
        content_type="text/html"))
    page.goto(ORIGIN + "/ide-shell.html")
    # A generated video stream exercises real video + iframe compositing without
    # contacting the host or loading external media.
    page.evaluate("""async () => {
        const canvas = document.createElement('canvas'); canvas.width = 1400; canvas.height = 900;
        const ctx = canvas.getContext('2d');
        const draw = () => {
            const fill = ctx.createLinearGradient(0, 0, 1400, 900);
            fill.addColorStop(0, '#164e63'); fill.addColorStop(.5, '#5b21b6'); fill.addColorStop(1, '#be185d');
            ctx.fillStyle = fill; ctx.fillRect(0, 0, 1400, 900);
        };
        const video = document.querySelector('video');
        video.srcObject = canvas.captureStream(10);
        draw(); setInterval(draw, 100);
        await video.play();
    }""")
    ide = page.frame_locator("iframe")
    ide.locator(".ide-tree-row[data-path='README.md']").wait_for()
    ide.locator("body").evaluate("el => { const s = document.createElement('style'); s.textContent = '* { transition: none !important; }'; document.head.append(s); }")
    ide.locator(".ide-tree-row[data-path='README.md']").click()
    ide.locator(".ide-ai-btn").click()
    chat = ide.frame_locator(".ide-chat-frame")
    chat.locator("#chatInput").wait_for()
    page.evaluate("""() => {
        const target = document.querySelector('iframe').contentWindow;
        target.postMessage({type:'cuttle-theme-change', theme:'sakura'}, location.origin);
        target.postMessage({type:'cuttle-video-state', active:true}, location.origin);
    }""")
    page.wait_for_function("""() => {
        const ide = document.querySelector('iframe').contentWindow;
        const events = ide.document.querySelector('.ide-chat-frame')?.contentWindow.appearance || [];
        return events.some(e => e.type === 'cuttle-theme-change' && e.theme === 'sakura')
            && events.some(e => e.type === 'cuttle-video-state' && e.active);
    }""")
    # The theme bridge updates inherited chrome ink as well as editor ink.
    assert ide.locator(".ide-title-left").evaluate("el => getComputedStyle(el).color") == "rgb(74, 43, 58)"
    page.screenshot(path="temp/ide-shell-video.png")
    # Sidebar remains usable in a narrow split pane, including the header controls.
    page.set_viewport_size({"width": 580, "height": 800})
    ide.locator("[data-view='search']").click()
    assert ide.locator("[data-view-panel='search']").is_visible()
    page.screenshot(path="temp/ide-shell-narrow.png")
    assert not page.errors


def test_menu_save_save_all_and_edit_commands(page, project):
    assert page.locator('#ideMenubar [data-cmd="file.save"]').is_disabled()
    page.click(".ide-tree-row[data-path='README.md']")
    page.wait_for_function("document.querySelector('.ide-textarea')?.value.startsWith('# Demo')")
    _type(page, "first change\n")
    page.click('#ideMenubar [data-cmd="file.save"]')
    page.wait_for_function("!document.querySelector('.ide-tab.is-dirty')")
    assert "first change" in (project / "README.md").read_text()
    _type(page, "second change\n")
    page.click(".ide-tree-row[data-path='src']")
    page.click(".ide-tree-row[data-path='src/app.py']")
    page.wait_for_function("document.querySelector('.ide-textarea')?.value.startsWith('def hello')")
    _type(page, "# another change\n")
    page.locator('#ideMenubar').get_by_role('menuitem', name='File', exact=True).click()
    page.locator('#ideMenubarPopup').get_by_role('menuitem', name=re.compile('Save All')).click()
    page.wait_for_function("!document.querySelector('.ide-tab.is-dirty')")
    assert "second change" in (project / "README.md").read_text()
    assert "# another change" in (project / "src/app.py").read_text()
    page.locator('#ideMenubar').get_by_role('menuitem', name='Edit', exact=True).click()
    page.locator('#ideMenubarPopup').get_by_role('menuitem', name=re.compile('Select All')).click()
    assert page.locator('.ide-textarea').evaluate('el => el.selectionEnd - el.selectionStart') == len(_editor_text(page))
    page.locator('#ideMenubar').get_by_role('menuitem', name='File', exact=True).focus()
    page.keyboard.press('ArrowDown')
    assert page.locator('#ideMenubarPopup').is_visible()
    page.keyboard.press('ArrowRight')
    assert page.locator('#ideMenubarPopup').get_attribute('aria-label') == 'Edit'
    page.keyboard.press('Escape')
    assert page.locator('#ideMenubarPopup').is_hidden()
    assert not page.errors


class ShellIdeHarness(Harness):
    def handle(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/img/'):
            target = WEB.parent / path.lstrip('/')
            if target.is_file():
                return route.fulfill(body=target.read_bytes(), content_type='image/png')
        if path == '/pending_editor_test.html':
            return route.fulfill(content_type='text/html', body='''<!doctype html><html><head>
            <link rel="stylesheet" href="/css/ui_boot.css"><script src="/js/shared/ui_boot.js"></script>
            <link rel="stylesheet" href="/css/shared_navigation.css"><link rel="stylesheet" href="/css/pending_changes.css">
            </head><body><div id="pendingChangesHost"></div>
            <script src="/js/git/diff_modal.js"></script><script src="/js/git/pending_changes_panel.js"></script>
            <script>window.panel = CuttlePendingChangesPanel.create({startExpanded:true,
            getProject:() => ({path:PROJECT_PATH,name:'Demo'}),
            onOpenDiff:(file,meta) => CuttleDiffModal.open({projectPath:PROJECT_PATH,repoRoot:meta.repoRoot,file})
            });</script></body></html>'''.replace('PROJECT_PATH', json.dumps(str(self.project))))
        if path == '/api/auth/me':
            return route.fulfill(json={'authenticated': True, 'user': {'id': 1, 'role': 'owner'}})
        if path.startswith('/api/') and not path.startswith(FORWARD):
            return route.fulfill(json={'success': True, 'data': {}, 'workspaces': [], 'columns': [], 'projects': []})
        return super().handle(route)


@pytest.fixture()
def shell_editor(browser, flask_client, project):
    (project / 'README.md').write_text('# Demo\nchanged on disk\n')
    (project / 'src' / 'app.py').write_text('def hello():\n    return "changed"\n')
    harness = ShellIdeHarness(flask_client, project)
    context = browser.new_context(viewport={'width': 1600, 'height': 950})
    layout = {'version': 2, 'orientation': 'horizontal', 'root': {
        'type': 'group', 'id': 'root', 'orientation': 'horizontal', 'children': [
            {'type': 'leaf', 'id': 'source', 'page': '/pending_editor_test.html', 'flex': '1 1 0%'}]}}
    context.add_init_script("if (!localStorage.getItem('shell_split_layout')) localStorage.setItem('shell_split_layout', " + json.dumps(json.dumps(layout)) + ")")
    pg = context.new_page()
    errors = []
    pg.on('pageerror', lambda error: errors.append(str(error) + '\n' + error.stack))
    pg.route('**/*', harness.handle)
    pg.goto(ORIGIN + '/app_shell.html')
    pg.wait_for_function("typeof getSplitColumns === 'function' && getSplitColumns().length === 1")
    source = pg.frame_locator('.shell-main iframe').first
    source.locator('.pending-changes-editor-btn').first.wait_for()
    pg.errors = errors
    yield pg
    context.close()


def test_pending_pencil_splits_then_reuses_ide_without_losing_edits(shell_editor, project):
    pg = shell_editor
    source = pg.frame_locator('.shell-main iframe').first
    pg.evaluate('window.originalSource = getLiveFrame(0).contentWindow')
    source.locator('.pending-changes-editor-btn[data-rel-path="README.md"]').click()
    pg.wait_for_function('getSplitColumns().length === 2')
    ide = pg.frame_locator('.split-column[data-column="1"] .shell-main iframe')
    ide.locator('.ide-tab[data-path="README.md"]').wait_for()
    ide.locator('.ide-textarea').fill('unsaved work to preserve')
    source.locator('.pending-changes-editor-btn[data-rel-path="src/app.py"]').click()
    ide.locator('.ide-tab.is-active[data-path="src/app.py"]').wait_for()
    assert ide.locator('.ide-tab').count() == 2
    assert pg.evaluate('getSplitColumns().length') == 2
    assert pg.evaluate('originalSource === getLiveFrame(0).contentWindow')
    source.locator('.pending-changes-editor-btn[data-rel-path="README.md"]').click()
    ide.locator('.ide-tab.is-active[data-path="README.md"]').wait_for()
    assert ide.locator('.ide-textarea').input_value() == 'unsaved work to preserve'
    assert 'unsaved work' not in (project / 'README.md').read_text()
    source.locator('.pending-changes-item[data-rel-path="src/app.py"]').click()
    source.locator('[data-diff="editor"]').click()
    ide.locator('.ide-tab.is-active[data-path="src/app.py"]').wait_for()
    assert pg.evaluate('getSplitColumns().length') == 2
    assert not pg.errors


def test_pending_pencils_hide_when_experiment_is_disabled(shell_editor):
    from api.experimental import flags
    source = shell_editor.frame_locator('.shell-main iframe').first
    flags.set_enabled('ide', False)
    source.locator('body').evaluate("""() => window.dispatchEvent(new MessageEvent('message', {
        origin:location.origin, data:{type:'cuttle-experimental-flags-changed'}
    }))""")
    source.locator('.pending-changes-editor-btn').first.wait_for(state='hidden')
    assert not shell_editor.errors


def test_quick_editor_requests_queue_while_booting_and_stay_in_current_space(shell_editor):
    pg = shell_editor
    source = pg.frames[1]
    source.evaluate("""() => {
        const path = panel.getRepos()[0].repo_root;
        for (const file of ['README.md', 'src/app.py'])
            CuttleDiffModal.openInEditor({projectPath:path, repoRoot:path, file});
    }""")
    ide = pg.frame_locator('.split-column[data-column="1"] .shell-main iframe')
    ide.locator('.ide-tab.is-active[data-path="src/app.py"]').wait_for()
    assert ide.locator('.ide-tab').count() == 2
    assert pg.evaluate('getSplitColumns().length') == 2
    # A space holding an IDE must not become the destination after switching.
    pg.evaluate("""() => {
        const state = shellSpaces.state;
        const next = CuttleSpaces.addSpaceToState(state);
        next.root = {type:'group',id:'second-root',orientation:'horizontal',children:[
            {type:'leaf',id:'second-source',page:'/pending_editor_test.html',flex:'1 1 0%'}]};
        shellSpaces.switchSpace(next.id);
    }""")
    pg.wait_for_function('getSplitColumns().length === 1')
    source = pg.frame_locator('.shell-main iframe').first
    source.locator('.pending-changes-editor-btn[data-rel-path="README.md"]').click()
    pg.wait_for_function('getSplitColumns().length === 2')
    ide = pg.frame_locator('.split-column[data-column="1"] .shell-main iframe')
    ide.locator('.ide-tab.is-active[data-path="README.md"]').wait_for()
    assert not pg.errors


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
