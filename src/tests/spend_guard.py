"""Gate for tests that spend real money/tokens.

Two families of live tests exist in this repo; both are OFF by default so a
plain ``pytest src/tests/`` (or an agent running a test file on its own
initiative) never spends anything:

1. Script-style diagnostics (``unit/test_api_key.py``,
   ``unit/test_openai_connection.py``). These send real prompts when executed
   directly or via the legacy ``run_*_tests.py`` runners. They must call
   :func:`require_spend` before the first network call and treat ``False``
   as "skip, report pass".

2. Pytest live tests (``test_agent_harness_smoke.py``,
   ``test_agent_resume_contract.py::test_live_cursor_resume_two_turn``).
   These stay under the existing ``CUTTLE_AGENT_SMOKE=1`` + scope convention
   AND must run inside ``allow_external_runners(...)`` so the fail-closed
   guard in ``api.agent_router.supervised.test_isolation`` can tell an
   explicit opt-in apart from an accident.

Rule for agents: never set these flags without asking the user first.
"""

from __future__ import annotations

import os

#: Set ``CUTTLE_ALLOW_SPEND=1`` to let script-style diagnostics send real
#: prompts. Never set this proactively — ask the user, it spends real money.
SPEND_ENV = "CUTTLE_ALLOW_SPEND"


def spend_enabled() -> bool:
    """True only when the user explicitly opted into real spend."""
    return os.environ.get(SPEND_ENV, "").strip() == "1"


def require_spend(action: str) -> bool:
    """Print a loud banner; return True iff spending is allowed.

    ``action`` names what would be spent, e.g.
    ``"a real gpt-3.5-turbo completion via the OpenAI API"``.
    Callers must skip (not fail) when this returns False.
    """
    if spend_enabled():
        print()
        print("!" * 60)
        print(f"[!] SPENDING REAL TOKENS: {action}")
        print(f"[!] ({SPEND_ENV}=1 was set explicitly)")
        print("!" * 60)
        print()
        return True
    print()
    print("[SKIP] This diagnostic would send a real prompt and spend real")
    print(f"       tokens/money: {action}.")
    print(f"       Refusing: {SPEND_ENV} is not set to \"1\".")
    print("       To opt in, ask the user first, then re-run with")
    print(f"       {SPEND_ENV}=1 in the environment.")
    print()
    return False
