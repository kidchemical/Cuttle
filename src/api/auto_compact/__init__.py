"""Auto-compaction: compact an agent's context between turns, before its CLI would.

``policy`` (pure, Cuttle-free: settings shape, layering, the decision) /
``store`` (SQLite in the Cuttle home: per-chat overrides, state, history,
cross-process lease) / ``service`` (gather a chat's snapshot, decide, run the
compact through ``api.agent_context``) / ``triggers`` (Flask-owned: a chat's
``turn_end`` arms a check after a quiet period) / ``routes`` (per-chat
override for the context gauge). Gate: experimental flag ``auto_compact``.
"""
