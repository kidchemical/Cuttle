"""Execution nodes: run a chat's agent CLI on a paired machine.

The host stays authoritative (accounts, history, context, settings); a node
is a device worker that opted in to ``agent_turn`` jobs. Owner map:
``model`` (pure + wire contract) / ``store`` (home SQLite) / ``service`` (host:
nodes, chat binding, kernel seam) / ``remote`` (host: the adapter that runs a
turn on a node) / ``node`` (node side: runs the CLI, relays status) /
``routes``. Runbook: ``.cuttle_global/features/exec_nodes/docs/execution-nodes.md``;
design and limits: ``docs/guides/EXECUTION_NODES.md``.
"""
