**Verdict:** Different panes with different chat sessions do **not** cancel/followup each other's workers (lookup is by `parent_session_id`). Cross-pane harm is still real via **shared global coordinator settings**, and there is a **control-lane bug** that can double-apply side effects.

### Findings

**[P1] Supervised control lane crashes on `_auth_user` after mutating state — then falls through and runs again** — [file:////path/to/Cuttle/src/api/web_chat_api.py](file:////path/to/Cuttle/src/api/web_chat_api.py) / [vscode://file//path/to/Cuttle/src/api/web_chat_api.py:5219](vscode://file//path/to/Cuttle/src/api/web_chat_api.py:5219)

`/coordinate cancel|followup|status` and `/coordinator …` run **before** `_resolve_auth_chat_session` (line 5299), but the handler still evaluates `_auth_user` at 5219. That raises `NameError`, the outer `except` swallows it, and the request continues into the later router-family path — which handles the same command again.

**[P1] `/coordinator` profile / worker / review-loops / reset are process-global** — [file:////path/to/Cuttle/src/api/agent_router/supervised/profiles.py](file:////path/to/Cuttle/src/api/agent_router/supervised/profiles.py)

**[P2] `awaiting_user` is still “active” in the index but not treated as busy** — [file:////path/to/Cuttle/src/api/agent_router/supervised/orchestrator.py](file:////path/to/Cuttle/src/api/agent_router/supervised/orchestrator.py)
