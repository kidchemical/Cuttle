"""Orchestrator (experimental flag ``orchestrator``, default off).

Playbooks describe how an orchestrator chat delegates to sub-agent child chats:
an ordered org chart of tiers (each a list of agent-profile ids), grind/retry
limits, supervisor defaults and notes. ``/orchestrate`` binds a chat to a
playbook; the Context Compiler then injects the rendered brief into every fresh
context for that chat, so a harness switch keeps the instructions.

* ``model``     — pure, Cuttle-free: schema v1, normalize, render brief, supervisor args.
* ``store``     — SQLite ``<home>/db/orchestrator.db`` (playbooks, chat bindings).
* ``service``   — host adapter: user scoping, profile resolution, flag gate, ``brief_block``.
* ``routes``    — owner-only HTTP + the Orchestrator and Agents App pages.
* ``__main__``  — agent verbs (``python -m api.orchestrator``).

Agent profiles stay owned by ``api.subagents.profiles``; the Agents App is a UI
over that store (routes live here because both Apps ship behind this flag).

Teardown: delete the flag row in ``api/experimental/features.py``, this package,
its blueprint block in ``web_chat_api.py``, the ``brief_block`` call in
``cuttle_brain/context_compiler.py``, ``orchestrator_page.html`` +
``agent_profiles_page.html`` + ``css/orchestrator_page.css`` + ``js/orchestrator/``,
the ``nav-orchestrator`` / ``nav-agents`` rail entries, and
``.cuttle_global/features/orchestrator/``. ``<home>/db/orchestrator.db`` may be
deleted; saved agent profiles are unaffected.
"""
