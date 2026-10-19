"""IDE agent review in a real browser against the real ``api.agent_review`` routes.

An agent turn is simulated through the same seams the harness kernel uses
(``agent_events.snapshots.begin/finish`` + ``agent_review.service`` hooks) —
no agent CLI runs. The review stream is not forwarded (the Flask test client
cannot hold an SSE response open), so the page exercises its polling fallback.

Inline hunks need Monaco: those tests run only with ``CUTTLE_IDE_E2E_MONACO=1``
(loads Monaco from jsDelivr). The review bar, badges and file-level actions
also run on the offline plain editor.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import pytest

from tests.e2e.test_ide_app import (  # noqa: F401  (fixtures)
    MONACO, ORIGIN, Harness, _editor_text, browser, flask_client, project,
)

ORIGINAL = "def hello():\n    return 'hi'\n"
# Two hunks with an unchanged line between: a new header, a changed return.
AGENT = "# greeting helpers\ndef hello():\n    return 'hello'\n"
monaco_only = pytest.mark.skipif(not MONACO, reason="inline hunks need Monaco (CUTTLE_IDE_E2E_MONACO=1)")


class ReviewHarness(Harness):
    def handle(self, route):
        path = urlparse(route.request.url).path
        if path.startswith("/api/agent-review/") and path != "/api/agent-review/stream":
            req = route.request
            res = self.client.open(path, method=req.method, query_string=urlparse(req.url).query,
                                   data=req.post_data_buffer, content_type=req.headers.get("content-type"))
            return route.fulfill(status=res.status_code, body=res.get_data(),
                                 headers={"content-type": "application/json"})
        return super().handle(route)


@pytest.fixture()
def agent_turn(project, flask_client, tmp_path, monkeypatch):
    """Run (or start) a fake agent turn: ``agent_turn(writes, finish=True)``."""
    from api.agent_events.snapshots import begin, finish
    from api.agent_review import service
    from api.experimental import flags

    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    flags.set_enabled("agent_review", True)
    count = {"n": 0}

    def run(writes, finish_turn=True):
        count["n"] += 1
        qid = f"e2e-{count['n']}"
        snap = begin(str(project), qid)
        service.turn_started(snap, query_id=qid, chat_session_id="9", agent_id="claude")
        for rel, text in writes.items():
            target = project / rel
            if text is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            service.note_agent_edit(qid, str(target))
        if finish_turn:
            service.turn_finished(qid, finish(snap))
        return qid
    return run


@pytest.fixture()
def open_ide(browser, flask_client, project):
    contexts = []

    def go():
        harness = ReviewHarness(flask_client, project)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_init_script("localStorage.clear()")
        contexts.append(context)
        pg = context.new_page()
        pg.route("**/*", harness.handle)
        pg.errors = []
        pg.on("pageerror", lambda exc: pg.errors.append(str(exc)))
        pg.goto(ORIGIN + "/ide_page.html")
        pg.wait_for_selector(".ide-tree-row[data-path='README.md']", timeout=15000)
        return pg
    yield go
    for context in contexts:
        context.close()


def _wait_disk(page, path, text, timeout=5):
    """Poll while pumping Playwright: routes answer requests on this thread."""
    deadline = time.time() + timeout
    while time.time() < deadline and (path.read_text(encoding="utf-8") if path.exists() else None) != text:
        page.wait_for_timeout(100)
    return path.read_text(encoding="utf-8") if path.exists() else None


def test_review_bar_badges_and_file_actions(agent_turn, open_ide, project):
    agent_turn({"src/app.py": AGENT, "notes.md": "agent notes\n"})
    page = open_ide()
    page.wait_for_selector("#ideReviewBar:not([hidden])", timeout=8000)
    assert "2 files" in page.inner_text("#ideReviewBar")
    assert page.inner_text("#ideStatusReview").startswith("✦")
    page.wait_for_selector(".ide-tree-row[data-path='src'] .ide-tree-review")
    assert page.locator(".ide-tree-row[data-path='notes.md'] .ide-tree-review").count() == 1
    page.dblclick(".ide-tree-row[data-path='notes.md']")
    page.wait_for_selector(".ide-tab[data-path='notes.md'] .ide-tab-ai")
    # Keep the file: disk untouched, review gone for it.
    page.click("#ideReviewBar [data-rb='keepFile']")
    page.wait_for_function("!document.querySelector('.ide-tab[data-path=\"notes.md\"] .ide-tab-ai')", timeout=8000)
    assert (project / "notes.md").read_text(encoding="utf-8") == "agent notes\n"
    assert "1 file" in page.inner_text("#ideReviewBar")
    # Undo all: confirm dialog, then disk is restored and the bar hides.
    page.click("#ideReviewBar [data-rb='undoAll']")
    page.click("#ideDialogActions button[value='ok']")
    page.wait_for_selector("#ideReviewBar", state="hidden", timeout=8000)
    assert _wait_disk(page, project / "src" / "app.py", ORIGINAL) == ORIGINAL
    assert page.locator("[data-banner='review']").count() == 0
    assert not page.errors


@monaco_only
def test_inline_hunks_keep_and_undo_in_monaco(agent_turn, open_ide, project):
    agent_turn({"src/app.py": AGENT})
    page = open_ide()
    page.dblclick(".ide-tree-row[data-path='src']")
    page.dblclick(".ide-tree-row[data-path='src/app.py']")
    page.wait_for_selector(".ide-review-actions", timeout=10000)
    assert page.locator(".ide-review-actions").count() == 2
    assert page.locator(".ide-review-removed-line").all_inner_texts() == ["    return 'hi'"]
    assert page.locator(".monaco-editor .ide-review-added").count() >= 2
    assert page.inner_text(".ide-tab[data-path='src/app.py'] .ide-tab-ai").strip() == "✦ 2"
    # Keep the first hunk (the header): baseline moves, disk unchanged.
    page.locator(".ide-review-actions").nth(0).locator("[data-review='keep']").click()
    page.wait_for_function("document.querySelectorAll('.ide-review-actions').length === 1", timeout=8000)
    assert (project / "src" / "app.py").read_text(encoding="utf-8") == AGENT
    # Undo the second (the return): disk and editor revert it, no stale banner.
    page.locator(".ide-review-actions [data-review='undo']").click()
    page.wait_for_selector("#ideReviewBar", state="hidden", timeout=8000)
    expected = "# greeting helpers\ndef hello():\n    return 'hi'\n"
    assert _wait_disk(page, project / "src" / "app.py", expected) == expected
    page.wait_for_function("t => monaco.editor.getModels().some(m => m.uri.path === '/src/app.py' && m.getValue() === t)", arg=expected)
    assert page.locator(".ide-tab.is-dirty").count() == 0
    assert page.locator("[data-banner='review']").count() == 0
    assert not page.errors


@monaco_only
def test_live_turn_shows_up_and_shortcuts_navigate(agent_turn, open_ide, project):
    page = open_ide()
    page.dblclick(".ide-tree-row[data-path='src']")
    page.dblclick(".ide-tree-row[data-path='src/app.py']")
    page.wait_for_selector(".monaco-editor")
    # The agent edits while the file is open; the turn is still running.
    agent_turn({"src/app.py": AGENT}, finish_turn=False)
    page.wait_for_selector(".ide-review-actions", timeout=12000)
    assert "Agent editing" in page.inner_text("#ideReviewBar")
    page.evaluate("() => document.querySelector('.monaco-editor textarea').focus()")
    page.keyboard.press("Alt+]")
    first = page.evaluate("() => monaco.editor.getEditors()[0].getPosition().lineNumber")
    page.keyboard.press("Alt+]")
    second = page.evaluate("() => monaco.editor.getEditors()[0].getPosition().lineNumber")
    assert first != second
    page.keyboard.press("Control+Alt+y")
    page.wait_for_function("document.querySelectorAll('.ide-review-actions').length === 1", timeout=8000)
    assert not page.errors
