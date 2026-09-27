"""
E2E: history panel search must survive refresh / auth-changed, and title
matches must rank above newer content-only matches.

Run:
  .venv\\Scripts\\python.exe -m pytest src/tests/e2e/test_chat_history_search_panel.py -v -s
"""
from __future__ import annotations

import os
import sys
import threading
import time
import http.server
import socketserver
from pathlib import Path

import pytest
import requests

src_root = Path(__file__).resolve().parent.parent.parent
if str(src_root) not in sys.path:
    sys.path.insert(0, str(src_root))

API_BASE_ENV = os.getenv("CUTTLE_API_URL", "http://127.0.0.1:8080")
STATIC_PORT_FALLBACK = 58315


def _server_available(base: str) -> bool:
    try:
        r = requests.get(f"{base}/api/health", timeout=2)
        return r.ok
    except Exception:
        return False


def _static_available(port: int) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/chat_page.html", timeout=2)
        return r.ok and "Cuttle" in r.text
    except Exception:
        return False


@pytest.fixture(scope="module")
def chat_base_url():
    if _server_available(API_BASE_ENV):
        yield API_BASE_ENV
        return
    if _static_available(STATIC_PORT_FALLBACK):
        yield f"http://127.0.0.1:{STATIC_PORT_FALLBACK}"
        return
    web_dir = src_root / "web"
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(web_dir), **k
    )
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler, bind_and_activate=False)
    httpd.allow_reuse_address = True
    httpd.server_bind()
    httpd.server_activate()
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    for _ in range(20):
        if _static_available(port):
            break
        time.sleep(0.2)
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


def _playwright_available():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture(scope="module")
def browser_page(chat_base_url):
    if not _playwright_available():
        pytest.skip("Playwright not installed")
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.goto(f"{chat_base_url}/chat_page.html", wait_until="domcontentloaded", timeout=20000)
    page.wait_for_selector("#chatInput, #welcomeChatInput, #searchInput", timeout=15000)
    yield page
    context.close()
    browser.close()
    pw.stop()


SEED_AND_SEARCH_JS = r"""
() => {
  const now = Date.now();
  const sessions = {
    title_old: {
      id: 'title_old',
      title: 'alpha widget design',
      updated: now - 7 * 24 * 3600 * 1000,
      messages: [
        { role: 'user', content: 'old chat about other things', timestamp: now - 7 * 24 * 3600 * 1000 }
      ]
    },
    content_new: {
      id: 'content_new',
      title: 'grocery list',
      updated: now,
      messages: [
        { role: 'user', content: 'please revisit the alpha widget tomorrow', timestamp: now }
      ]
    },
    decoy: {
      id: 'decoy',
      title: 'unrelated meeting notes',
      updated: now - 1000,
      messages: [
        { role: 'user', content: 'standup agenda', timestamp: now - 1000 }
      ]
    }
  };
  localStorage.setItem('chatSessions', JSON.stringify(sessions));
  if (window.loadChatHistory) window.loadChatHistory();
  if (window.openChatHistoryPanel) window.openChatHistoryPanel();
  const input = document.getElementById('searchInput');
  if (!input) return { ok: false, reason: 'no searchInput' };
  input.value = 'alpha widget';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  if (window.searchChats) window.searchChats();
  const ids = Array.from(document.querySelectorAll('#chatHistory .chat-history-item'))
    .map((el) => el.getAttribute('data-session-id'));
  const tiers = Array.from(document.querySelectorAll('#chatHistory [data-search-tier]'))
    .map((el) => el.getAttribute('data-search-tier'));
  const titles = Array.from(
    document.querySelectorAll('#chatHistory [data-search-tier="__search_titles__"] .chat-history-item')
  ).map((el) => el.getAttribute('data-session-id'));
  const content = Array.from(
    document.querySelectorAll('#chatHistory [data-search-tier="__search_content__"] .chat-history-item')
  ).map((el) => el.getAttribute('data-session-id'));
  return { ok: true, ids, tiers, titles, content, searchValue: input.value };
}
"""


def _history_ids(page):
    return page.evaluate("""() => Array.from(
      document.querySelectorAll('#chatHistory .chat-history-item')
    ).map((el) => el.getAttribute('data-session-id'))""")


def test_search_survives_auth_changed_and_refresh(browser_page):
    page = browser_page
    seeded = page.evaluate(SEED_AND_SEARCH_JS)
    assert seeded.get("ok"), seeded
    assert seeded["ids"] == ["title_old", "content_new"], seeded
    assert "decoy" not in seeded["ids"]
    assert seeded["titles"] == ["title_old"], seeded
    assert seeded["content"] == ["content_new"], seeded

    # The bug: cuttle-auth-changed / refreshChatHistoryList painted the full list
    # while the search box still had a query.
    page.evaluate("""() => {
      window.dispatchEvent(new CustomEvent('cuttle-auth-changed', { detail: {} }));
      if (window.refreshChatHistoryList) window.refreshChatHistoryList();
      if (window.loadChatHistory) window.loadChatHistory();
    }""")
    page.wait_for_timeout(400)

    after = page.evaluate("""() => {
      const input = document.getElementById('searchInput');
      const ids = Array.from(document.querySelectorAll('#chatHistory .chat-history-item'))
        .map((el) => el.getAttribute('data-session-id'));
      const titles = Array.from(
        document.querySelectorAll('#chatHistory [data-search-tier="__search_titles__"] .chat-history-item')
      ).map((el) => el.getAttribute('data-session-id'));
      const content = Array.from(
        document.querySelectorAll('#chatHistory [data-search-tier="__search_content__"] .chat-history-item')
      ).map((el) => el.getAttribute('data-session-id'));
      return { ids, titles, content, searchValue: input && input.value, itemCount: ids.length };
    }""")
    assert after["searchValue"] == "alpha widget"
    assert after["ids"] == ["title_old", "content_new"], after
    assert after["titles"] == ["title_old"], after
    assert after["content"] == ["content_new"], after
    assert "decoy" not in after["ids"], after


def test_search_survives_cleared_input_dom_while_live_query_latched(browser_page):
    """Capacitor/IME can briefly desync the input DOM from the applied query."""
    page = browser_page
    seeded = page.evaluate(SEED_AND_SEARCH_JS)
    assert seeded.get("ok"), seeded

    after = page.evaluate("""() => {
      const input = document.getElementById('searchInput');
      // Simulate Android WebView losing the input value mid-refresh while the
      // applied search latch is still active.
      if (input) input.value = '';
      if (window.loadChatHistory) window.loadChatHistory();
      if (window.refreshChatHistoryList) window.refreshChatHistoryList();
      const ids = Array.from(document.querySelectorAll('#chatHistory .chat-history-item'))
        .map((el) => el.getAttribute('data-session-id'));
      const tiers = Array.from(document.querySelectorAll('#chatHistory [data-search-tier]'))
        .map((el) => el.getAttribute('data-search-tier'));
      return { ids, tiers, decoy: ids.includes('decoy') };
    }""")
    assert after["tiers"] == ["__search_titles__", "__search_content__"], after
    assert after["ids"] == ["title_old", "content_new"], after
    assert after["decoy"] is False


def test_title_match_ranks_above_newer_content_match(browser_page):
    page = browser_page
    seeded = page.evaluate(SEED_AND_SEARCH_JS)
    assert seeded.get("ok"), seeded
    assert seeded["tiers"] == ["__search_titles__", "__search_content__"], seeded
    assert seeded["titles"] == ["title_old"], seeded
    assert seeded["content"] == ["content_new"], seeded
    ids = seeded["ids"]
    assert ids[0] == "title_old", f"title match should rank first, got {ids}"
    assert ids[1] == "content_new", f"content match should be second, got {ids}"
    assert _history_ids(page) == ["title_old", "content_new"]
