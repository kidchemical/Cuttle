"""History delete uses a pane-centered confirm modal, not inline checkmarks."""

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
CHAT_JS = WEB / "js" / "chat/chat_page.js"
CHAT_CSS = WEB / "css" / "chat_page.css"
CHAT_HTML = WEB / "chat_page.html"


def test_history_delete_modal_lives_in_the_pane_not_the_sidebar():
    html = CHAT_HTML.read_text(encoding="utf-8")
    panel_start = html.find('id="chatHistoryPanel"')
    panel_end = html.find("</aside>", panel_start)
    assert panel_start > 0 and panel_end > panel_start
    panel = html[panel_start:panel_end]
    layout_start = html.find('class="chat-layout"')
    main_end = html.find("</main>", layout_start)
    assert 'id="historyDeleteModal"' not in panel
    # Pane overlay: a sibling after </main> inside .chat-layout (other pane
    # modals such as git-push confirm may sit between them).
    modal = html.find('id="historyDeleteModal"')
    assert layout_start < main_end < modal
    modal_end = html.find('id="historyRenameModal"', modal)
    for child in ('id="historyDeleteConfirm"', 'id="historyDeleteCancel"'):
        assert modal < html.find(child) < modal_end
    assert "chat-delete-confirm" not in html


def test_history_delete_click_opens_modal_not_row_checkmark():
    js = CHAT_JS.read_text(encoding="utf-8")
    assert "function openHistoryDeleteModal(" in js
    assert "function requestDeleteChatSession(" in js
    req_start = js.find("function requestDeleteChatSession(")
    req = js[req_start : req_start + 700]
    assert "openHistoryDeleteModal(" in req
    assert "confirming-delete" not in js
    assert "chat-delete-confirm" not in js
    assert "chatPageRequestDeleteSession" in js


def test_history_delete_modal_css_is_pane_centered():
    css = CHAT_CSS.read_text(encoding="utf-8")
    assert ".history-delete-modal {" in css
    block_start = css.find(".history-delete-modal {")
    block = css[block_start : block_start + 400]
    assert "position: fixed" in block
    assert "inset: 0" in block
    assert "justify-content: center" in block
    assert "align-items: center" in block
    assert "position: absolute" not in block
    assert ".chat-delete-confirm" not in css
    assert "confirming-delete" not in css
