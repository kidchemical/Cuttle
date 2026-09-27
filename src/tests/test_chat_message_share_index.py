"""CH-<session>-N share indices must match the UI, not raw SQLite row order.

Regression from CH-000430 / CH-000464: Stop generation persists
``role=system`` notices ("⏹ Stopped generating."). The UI share button and
message-nav number those bubbles among ``.message:not(.system)`` only
(user+assistant). Agents that take ``ORDER BY id`` over *all* rows and then
``rows[N-1]`` land on the wrong bubble — often an earlier user turn — and
look like a "desync" after Stop.

CH-000430-98 on the user's screen was the Cycles dark-center note; a naive
all-rows lookup returned an earlier "show me the other side by sides" turn
because eight stop notices sat earlier in the transcript.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.auth_db import AuthDatabase
from api.cuttle_ui_capabilities import parse_chat_handle, resolve_chat_handle_message


STOP = "⏹ Stopped generating."


def _session(tmp_path: Path) -> tuple[AuthDatabase, int]:
    db = AuthDatabase(tmp_path / "cuttle_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    sid = db.create_chat_session(owner, "share-index")
    return db, sid


def _naive_all_rows_index(db: AuthDatabase, sid: int, n: int) -> dict | None:
    """The broken agent recipe: count every row, including system stops."""
    rows = db.get_messages(sid)
    if n < 1 or n > len(rows):
        return None
    return rows[n - 1]


def test_share_index_skips_stop_system_rows(tmp_path: Path):
    """Stop notices must not consume a CH-xxx-N slot (UI contract)."""
    db, sid = _session(tmp_path)

    db.add_message(sid, "user", "side by sides please")
    db.add_message(sid, "assistant", "here are twelve")
    db.add_message(sid, "system", STOP, metadata={"kind": "generation-stop"})
    db.add_message(sid, "user", "show me the other side by sides")
    db.add_message(sid, "assistant", "markdown images…")
    for _ in range(3):
        db.add_message(sid, "system", STOP, metadata={"kind": "generation-stop"})
    cycles = (
        "this looks really good! one thing im not liking in cycles is that "
        "the center of the image is darker"
    )
    db.add_message(sid, "user", cycles)
    db.add_message(sid, "system", STOP, metadata={"kind": "generation-stop"})
    db.add_message(sid, "user", "look at frame 132")

    # Visible (user+assistant) order:
    # 1 side-by-sides ask, 2 assistant, 3 other side-by-sides, 4 assistant,
    # 5 cycles darker, 6 frame 132
    msg = db.get_message_by_share_index(sid, 5)
    assert msg is not None
    assert msg["role"] == "user"
    assert "center of the image is darker" in msg["content"]

    handle = f"CH-{sid:06d}-5"
    resolved = resolve_chat_handle_message(handle, db=db)
    assert resolved is not None
    assert resolved["id"] == msg["id"]
    assert parse_chat_handle(handle) == {"session_id": sid, "message_index": 5}


def test_naive_all_rows_index_desyncs_after_stops(tmp_path: Path):
    """Captures the agent bug: all-rows[N] ≠ UI share index N after Stop."""
    db, sid = _session(tmp_path)

    db.add_message(sid, "user", "early ask")
    db.add_message(sid, "assistant", "early reply")
    # Eight stops — same class of skew as CH-000430 before bubble 98.
    for _ in range(8):
        db.add_message(sid, "system", STOP, metadata={"kind": "generation-stop"})
    db.add_message(sid, "user", "wrong bubble if counted with stops")
    db.add_message(sid, "assistant", "padding")
    target = (
        "this looks really good! one thing im not liking in cycles is that "
        "the center of the image is darker"
    )
    db.add_message(sid, "user", target)

    # After 2 visible + 8 stops + 2 visible, the Cycles note is share index 5.
    share_n = 5
    correct = db.get_message_by_share_index(sid, share_n)
    naive = _naive_all_rows_index(db, sid, share_n)

    assert correct is not None and target in correct["content"]
    assert naive is not None
    assert naive["id"] != correct["id"]
    # With 8 stops after the first pair, all-rows[5] is still inside the stop run.
    assert naive["role"] == "system"
    assert "Stopped generating" in naive["content"]


def test_stop_after_message_does_not_renumber_earlier_share_index(tmp_path: Path):
    """A later Stop must not change the CH-ref of an earlier user bubble."""
    db, sid = _session(tmp_path)
    db.add_message(sid, "user", "alpha")
    db.add_message(sid, "assistant", "beta")
    mid = db.add_message(sid, "user", "gamma — stable ref")
    before = db.get_message_by_share_index(sid, 3)
    assert before is not None and before["id"] == mid

    db.add_message(sid, "system", STOP, metadata={"kind": "generation-stop"})
    db.add_message(sid, "user", "delta after stop")

    after = db.get_message_by_share_index(sid, 3)
    assert after is not None and after["id"] == mid
    assert db.get_message_by_share_index(sid, 4)["content"] == "delta after stop"


def test_share_index_out_of_range_and_session_only_handle(tmp_path: Path):
    db, sid = _session(tmp_path)
    db.add_message(sid, "user", "only")
    assert db.get_message_by_share_index(sid, 0) is None
    assert db.get_message_by_share_index(sid, 2) is None
    assert resolve_chat_handle_message(f"CH-{sid:06d}", db=db) is None
    assert resolve_chat_handle_message(f"CH-{sid:06d}-1", db=db)["content"] == "only"


def test_older_visible_count_aligns_with_share_index_base(tmp_path: Path):
    """Lazy-load base must count the same non-system rows as share indices."""
    db, sid = _session(tmp_path)
    ids = []
    for i in range(6):
        ids.append(db.add_message(sid, "user" if i % 2 == 0 else "assistant", f"v{i}"))
        if i in (1, 3):
            db.add_message(sid, "system", STOP, metadata={"kind": "generation-stop"})

    # Window starts at v2 (id = ids[2]); older visible = v0,v1 → 2
    meta = db.message_page_meta(sid, ids[2])
    assert meta["older_visible_count"] == 2
    assert db.get_message_by_share_index(sid, 3)["content"] == "v2"
    # UI would paint indexBase=2, then local i=0 → share index 3 for v2.


def test_runbook_teaches_cli_and_non_system_share_index():
    """Docs must teach chat_cli + user+assistant indexing (not all-rows[N-1])."""
    hub = Path(__file__).resolve().parents[2] / ".cuttle" / "docs" / "chat-history.md"
    personal = (
        Path(__file__).resolve().parents[2]
        / ".cuttle"
        / "personal"
        / "docs"
        / "chat-history.md"
    )
    bodies = [hub.read_text(encoding="utf-8")]
    if personal.exists():
        bodies.append(personal.read_text(encoding="utf-8"))

    for body in bodies:
        assert "api.chat_cli" in body
        assert "user+assistant" in body or "user + assistant" in body or "non-system" in body
        assert "system" in body.lower()
        assert "role != 'system'" in body or 'role != "system"' in body or (
            "role IN ('user', 'assistant')" in body
            or 'role IN ("user", "assistant")' in body
        )


@pytest.mark.parametrize(
    "role",
    ["system"],
)
def test_system_only_transcript_has_no_share_indices(tmp_path: Path, role: str):
    db, sid = _session(tmp_path)
    db.add_message(sid, role, STOP, metadata={"kind": "generation-stop"})
    assert db.get_message_by_share_index(sid, 1) is None
