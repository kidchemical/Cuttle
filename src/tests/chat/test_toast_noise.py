"""Toast noise: small chrome, one history entry per push.

Regression: toasts rendered large (380px / 14px) and every git push left
two notification-history entries (sticky "Pushing …" progress + outcome).
Progress toasts now opt out of history via ``skipHistory`` (forwarded
through the chat-iframe → shell postMessage hop), and the push outcome
reuses the progress toast id so it swaps in place.
"""

from __future__ import annotations

from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
TOAST_JS = WEB / "js" / "shared" / "toast.js"
CHAT_JS = WEB / "js" / "chat" / "chat_page.js"


def test_toast_chrome_is_compact():
    src = TOAST_JS.read_text(encoding="utf-8")
    assert "max-width: 300px;" in src
    assert "font-size: 13px;" in src
    assert "max-width: 380px" not in src


def test_progress_toasts_can_skip_history():
    src = TOAST_JS.read_text(encoding="utf-8")
    assert "if (!options.skipHistory) pushNotificationHistory(message, variant, options);" in src
    # Chat iframes forward toasts to the shell — the flag must survive the hop.
    assert "skipHistory: !!options.skipHistory," in src
    assert "skipHistory: !!e.data.skipHistory," in src


def test_push_leaves_one_history_entry_and_one_toast():
    src = CHAT_JS.read_text(encoding="utf-8")
    progress = src.split("toast('Pushing '", 1)[1].split("});", 1)[0]
    assert "toastId: 'git-push'" in progress
    assert "skipHistory: true" in progress
    assert "toast(okMsg, 'success', { toastId: 'git-push' });" in src


def test_push_repo_prefix_only_when_multi_repo():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "if (repoLabel && Array.isArray(repos) && repos.length > 1) okMsg = repoLabel + ': ' + okMsg;" in src
