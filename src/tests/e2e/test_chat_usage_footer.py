"""Actual chat history rendering with an isolated API; no Flask or agent calls."""
import time

from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_shared_diff_modal import browser, static_server  # noqa: F401


def test_muse_history_footer_clarifies_included_cache(browser, static_server):
    world = CardWorld([])
    world.history.append({
        "id": 2, "role": "assistant", "content": "Usage fixture",
        "timestamp": time.time() * 1000,
        "metadata": {
            "slash_command": {"chips": [{"category": "muse", "label": "Muse Code", "meta": "/muse"}]},
            # Older stored usage has no cache convention stamp.
            "usage": {"prompt_tokens": 9457534, "completion_tokens": 28014,
                "cache_read_tokens": 9413333, "cost": .02885, "cost_estimated": True},
        },
    })
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        footer = frame.locator(".message.assistant .message-usage")
        footer.wait_for(state="visible")
        assert "44k" in footer.inner_text()
        assert "9.4M" in footer.inner_text()
        assert "input" not in footer.inner_text()
        assert "cached" not in footer.inner_text()
        assert "~$0.029" in footer.inner_text()
        cache = footer.locator(".message-usage-cache")
        tip = cache.get_attribute("title") or cache.get_attribute("data-tooltip") or ""
        assert "99.5% of total input" in tip
        assert not errors
    finally:
        page.close()
