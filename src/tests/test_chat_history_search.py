"""Chat history panel search: titles first, then message bodies."""

from __future__ import annotations

from pathlib import Path

from api.auth_db import AuthDatabase


WEB = Path(__file__).resolve().parents[1] / "web"
CHAT_JS = WEB / "js" / "chat_page.js"


def _seed(tmp_path: Path):
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    other = db.create_user("other@local", "Other", "local", password="x")
    titled = db.create_chat_session(owner, "Flask restart drain")
    db.add_message(titled, "user", "please add a status card")
    db.add_message(titled, "assistant", "restart card is clickable")

    body_only = db.create_chat_session(owner, "Grocery list")
    db.add_message(body_only, "user", "the llama-server failed to bind port 8080")
    db.add_message(body_only, "assistant", "check the daemon log")

    empty = db.create_chat_session(owner, "Flask restart leftover")

    foreign = db.create_chat_session(other, "Flask restart drain")
    db.add_message(foreign, "user", "llama-server failed")

    token = db.create_auth_session(owner)
    return db, owner, token, titled, body_only, empty, foreign


def test_search_titles_before_message_bodies(tmp_path: Path):
    db, owner, _token, titled, body_only, empty, _foreign = _seed(tmp_path)
    hits = db.search_user_chats(owner, "Flask restart")
    ids = [row["id"] for row in hits]
    kinds = {row["id"]: row["match"] for row in hits}

    assert titled in ids
    assert empty not in ids
    assert kinds[titled] == "title"
    assert body_only not in ids

    body_hits = db.search_user_chats(owner, "llama-server failed")
    body_ids = [row["id"] for row in body_hits]
    assert body_only in body_ids
    assert titled not in body_ids
    match = next(row for row in body_hits if row["id"] == body_only)
    assert match["match"] == "content"
    assert "llama-server" in (match.get("snippet") or "")


def test_older_title_match_ranks_above_newer_content_match(tmp_path: Path):
    """Title hits stay ahead of content hits even when the content chat is newer."""
    db = AuthDatabase(tmp_path / "rank.db")
    owner = db.create_user("rank@local", "Rank", "local", password="x")

    old_title = db.create_chat_session(owner, "alpha widget design")
    db.add_message(old_title, "user", "old title chat body")
    new_content = db.create_chat_session(owner, "unrelated grocery list")
    db.add_message(new_content, "user", "please revisit the alpha widget tomorrow")

    conn = db._get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE chat_messages SET timestamp = datetime('now', '-7 days') WHERE chat_session_id = ?",
        (old_title,),
    )
    cur.execute(
        "UPDATE chat_sessions SET last_activity = datetime('now', '-7 days') WHERE id = ?",
        (old_title,),
    )
    cur.execute(
        "UPDATE chat_messages SET timestamp = datetime('now') WHERE chat_session_id = ?",
        (new_content,),
    )
    cur.execute(
        "UPDATE chat_sessions SET last_activity = datetime('now') WHERE id = ?",
        (new_content,),
    )
    conn.commit()
    conn.close()

    hits = db.search_user_chats(owner, "alpha widget")
    assert len(hits) >= 2
    assert hits[0]["id"] == old_title
    assert hits[0]["match"] == "title"
    content_row = next(r for r in hits if r["id"] == new_content)
    assert content_row["match"] == "content"
    assert hits.index(content_row) > 0


def test_search_matches_chat_handle(tmp_path: Path):
    db, owner, _token, titled, _body_only, _empty, _foreign = _seed(tmp_path)
    handle = f"CH-{titled:06d}"
    hits = db.search_user_chats(owner, handle)
    assert [row["id"] for row in hits] == [titled]
    assert hits[0]["match"] == "title"


def test_search_endpoint_scopes_to_owner(tmp_path: Path, monkeypatch):
    db, _owner, token, titled, body_only, _empty, foreign = _seed(tmp_path)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    from api import web_chat_api as wca

    client = wca.app.test_client()
    client.set_cookie("session_token", token)

    res = client.get("/api/auth/sessions/search", query_string={"q": "Flask restart"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["success"] is True
    ids = [row["id"] for row in body["sessions"]]
    assert titled in ids
    assert foreign not in ids

    res2 = client.get("/api/auth/sessions/search", query_string={"q": "llama-server failed"})
    ids2 = [row["id"] for row in res2.get_json()["sessions"]]
    assert body_only in ids2
    assert titled not in ids2
    assert foreign not in ids2


def test_history_js_hard_gates_full_list_paint_while_searching():
    """Regression: auth-changed / poll used to call loadChatHistory and wipe search."""
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "function historySearchQueryActive(" in js
    assert "historySearchLiveQuery" in js
    assert "function softRefreshHistorySearchIndicators(" in js
    assert "function reloadHistoryListKeepingSearch(" in js
    load_fn = js[js.find("function loadChatHistory(") : js.find("function loadChatHistory(") + 500]
    assert "historySearchQueryActive()" in load_fn
    assert "softRefreshHistorySearchIndicators()" in load_fn
    # Init listener (not waitForAuthReady) must keep search on auth settle.
    marker = "off the welcome splash"
    auth_idx = js.find(marker)
    assert auth_idx > 0
    auth_block = js[auth_idx : auth_idx + 1200]
    assert "reloadHistoryListKeepingSearch()" in auth_block
    assert "loadChatHistory();" not in auth_block
    # The settle path may reload projects first; that reload must keep search too.
    if "loadProjects();" in auth_block:
        lp_start = js.find("async function loadProjects(")
        lp_end = js.find("\n    }\n", lp_start)
        load_projects = js[lp_start:lp_end]
        assert "reloadHistoryListKeepingSearch()" in load_projects
        assert "loadChatHistory();" not in load_projects
    # Title matches sort above content matches in the sidebar.
    assert "entrySearchMatchRank" in js
    assert "title matches before content" in js.lower() or "Matching titles" in js
    assert "data-search-tier" in js
    assert "In messages" in js


def test_chat_page_asset_versions_bump_for_capacitor_cache():
    """Capacitor WebViews cache ?v= JS/CSS as immutable — bump when search UI changes."""
    import re

    html = (WEB / "chat_page.html").read_text(encoding="utf-8")
    # Date-prefixed cache-busters, never older than the search-wipe fix
    # (exact strings change on every bump).
    for asset in ("chat_page.js", "chat_page.css"):
        m = re.search(re.escape(asset) + r"\?v=(\d{8})\w*", html)
        assert m, f"{asset} lost its ?v= cache-buster"
        assert int(m.group(1)) >= 20260927, f"{asset} version older than the search fix"
    # Stale fingerprints must not linger (phone app would keep the wipe bug).
    assert "historyIconsFix" not in html
    assert "20260922codexModel" not in html
