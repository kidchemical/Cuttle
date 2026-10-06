"""Discord REST agent-ops (no inbound gateway)."""

from __future__ import annotations

import pytest

from api.discord_ops.post import resolve_post_channel_id


def test_resolve_alias():
    cid, key = resolve_post_channel_id(
        {"channels": {"feature-updates": "555"}},
        {"channel": "feature-updates"},
    )
    assert cid == "555"
    assert key == "feature-updates"


def test_resolve_allowlisted_snowflake():
    cid, key = resolve_post_channel_id(
        {"channels": {"feature-updates": "555"}},
        {"channel": "555"},
    )
    assert cid == "555"
    assert key == "555"


def test_resolve_rejects_unknown_snowflake():
    with pytest.raises(ValueError, match="Unknown Discord channel"):
        resolve_post_channel_id(
            {"channels": {"feature-updates": "555"}},
            {"channel": "999000111222"},
        )
