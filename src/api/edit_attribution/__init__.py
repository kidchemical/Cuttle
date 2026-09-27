"""Observed edit attribution for Cuttle harness runs → git commits.

See ``journal.py`` and ``recorder.py``. Proven worktree deltas only.
"""

from api.edit_attribution.journal import (
    attribution_summary_lines,
    build_commit_attribution,
    format_attribution_trailers,
    merge_message_with_trailers,
    settle_events,
)
from api.edit_attribution.recorder import record_run_deltas, snapshot_for_attribution

__all__ = [
    "attribution_summary_lines",
    "build_commit_attribution",
    "format_attribution_trailers",
    "merge_message_with_trailers",
    "record_run_deltas",
    "settle_events",
    "snapshot_for_attribution",
]
