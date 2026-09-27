"""Concurrent chats must not block on the query tracker lock."""

from __future__ import annotations

import threading
import time

from api.query_tracker import (
    _active_trackers,
    _registry_lock,
    finish_query_tracking,
    get_query_tracker,
    start_query_tracking,
)


def test_two_chats_can_start_query_tracking_concurrently():
    """Regression: second start_query used to block until the first finish_query (900s CLI)."""
    started = []
    finished = []
    errors = []

    def run(label: str, hold_s: float):
        try:
            qid = start_query_tracking(f"prompt-{label}", {"web_ui": True, "label": label})
            started.append((label, qid, time.time()))
            # Same thread must see its own tracker.
            assert get_query_tracker().query_id == qid
            assert get_query_tracker(qid).query_id == qid
            time.sleep(hold_s)
            finish_query_tracking(success=True)
            finished.append((label, qid, time.time()))
        except Exception as e:
            errors.append((label, e))

    t1 = threading.Thread(target=run, args=("a", 0.35), daemon=True)
    t2 = threading.Thread(target=run, args=("b", 0.05), daemon=True)
    t0 = time.time()
    t1.start()
    time.sleep(0.05)  # ensure first has acquired its instance lock
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, errors
    assert len(started) == 2
    assert len(finished) == 2
    # Second query must begin while the first is still holding its session open.
    started.sort(key=lambda x: x[2])
    first_start = started[0][2]
    second_start = started[1][2]
    assert second_start - first_start < 0.25, (
        f"second start_query blocked too long ({second_start - first_start:.3f}s); "
        "global tracker lock likely still serializing chats"
    )
    assert second_start - t0 < 0.5
    with _registry_lock:
        assert len(_active_trackers) == 0
