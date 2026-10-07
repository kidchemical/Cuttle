"""Native Muse prompts carry the runbook and session-scoped chat-store location."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from api.cuttle_ui_capabilities import chat_store_path, cuttle_chat_store_addon

REPO_ROOT = Path(__file__).resolve().parents[3]

# `sqlite3` writes this exact 16-byte magic at offset 0 of every database.
SQLITE_MAGIC = b"SQLite format 3\x00"






# --------------------------------------------------------------------------
# Root cause 1 — transcripts are a gitignored binary blob, invisible to search
# --------------------------------------------------------------------------


def test_chat_store_is_a_real_sqlite_file_outside_git(monkeypatch):
    """The transcripts live in a binary file outside the repository.

    Code-search tools only walk the workspace, so an agent that greps it
    cannot see chat content no matter how hard it looks.
    """
    import api.auth_db as auth_db

    # Resolve the production path (conftest redirects DB_PATH to tmp).
    monkeypatch.setattr(auth_db, "DB_PATH", auth_db.data_db_dir() / "cuttle_auth.db")
    db = chat_store_path()
    assert db, "chat store path must resolve for the prompt addon to be useful"
    db_path = Path(db)
    assert db_path.name == "cuttle_auth.db"
    assert not db_path.resolve().is_relative_to(REPO_ROOT.resolve()), (
        f"{db_path} is inside the repository — the search-invisibility premise "
        "of the Muse chat-store addon has changed"
    )

    if not db_path.exists():
        pytest.skip("no chat database on this machine yet")
    assert db_path.read_bytes()[:16] == SQLITE_MAGIC


def test_searching_tracked_files_finds_no_chat_content():
    """Reproduces the failure: a gitignore-respecting search yields only source.

    Every hit for "chat_messages" is a tracked `.py`/`.md` file — code *about*
    chats. That is the "one test that mentioned it" the user saw.
    """
    found = subprocess.run(
        ["git", "grep", "-l", "chat_messages"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True, encoding="utf-8",
        check=False,
    )
    hits = [line for line in found.stdout.splitlines() if line.strip()]
    assert hits, "expected source files that mention chat_messages"
    assert not any(h.endswith(".db") for h in hits), (
        "git grep reached a database file; the premise of this test is wrong"
    )








# --------------------------------------------------------------------------
# Root cause 3 — the /muse prompt never carried this context (regression)
# --------------------------------------------------------------------------


def test_chat_store_addon_names_the_db_and_warns_off_empty_searches():
    from pathlib import Path

    addon = cuttle_chat_store_addon()
    assert "cuttle_auth.db" in addon or "chat-history.md" in addon
    assert "chat-history.md" in addon
    assert "gitignored" in addon.lower() or "not in the working tree" in addon.lower()

    runbook = Path(__file__).resolve().parents[3] / ".cuttle_global" / "docs" / "chat-history.md"
    body = runbook.read_text(encoding="utf-8")
    assert "cuttle_auth.db" in body
    assert "chat_messages" in body
    assert "no traces of Cuttle chats" in body
    assert "leading zeros" in body.lower()




def test_muse_prompt_carries_chat_context_when_the_user_asks_about_chats(monkeypatch):
    """Regression: `/muse` built its prompt with no pane map and no DB pointer.

    `_run_harness_web_command("muse", …)` only called `with_cuttle_ui_capabilities`, unlike the
    remote-agent path which prepends `_build_shell_pane_prompt_addon`.
    """
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod

    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "native", "path": "/x"})

    enriched = w._with_muse_chat_context("summarize my recent Cuttle chats")

    assert "Cuttle chat history" in enriched
    assert "cuttle_auth.db" in enriched
    assert enriched.endswith("summarize my recent Cuttle chats")


def test_muse_prompt_carries_chat_context_for_ch_session_handle(monkeypatch):
    """CH-000149 failure: 'summarize CH-000147' never matched the word-only regex."""
    if sys.platform != "win32":
        pytest.skip("Muse chat-store enrichment is Windows-host specific")
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod

    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "native", "path": "/x"})
    monkeypatch.setattr(w, "_build_shell_pane_prompt_addon", lambda _p: "")

    prompt = "Can you summarize CH-000147?"
    enriched = w._with_muse_chat_context(prompt)

    assert "Cuttle chat history" in enriched
    assert "cuttle_auth.db" in enriched
    assert "CH-000147" in enriched  # the user's own handle survives the prefix
    assert enriched.endswith(prompt)


def test_muse_prompt_stays_lean_for_unrelated_work(monkeypatch):
    """Coding prompts must not pay for the chat-store block every turn."""
    from api import web_chat_api as w

    monkeypatch.setattr(w, "_build_shell_pane_prompt_addon", lambda _p: "")

    prompt = "refactor the retry loop in agent_process.py"
    assert w._with_muse_chat_context(prompt) == prompt


def test_muse_execution_sends_the_enriched_prompt_to_the_cli(tmp_path, monkeypatch):
    """End-to-end through `_run_harness_web_command("muse", …)`: the CLI must receive it."""
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod
    from scripts.utilities.muse_cli_tool import MuseCliTool

    seen = {}

    async def fake_execute_prompt(self, prompt, *args, **kwargs):
        seen["prompt"] = prompt
        return {"success": True, "output": "ok", "usage": {}}

    monkeypatch.setattr(muse_mod, "muse_available", lambda: True)
    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "native", "path": "/x"})
    monkeypatch.setattr(MuseCliTool, "execute_prompt", fake_execute_prompt)
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path))

    res = w._run_harness_web_command(
        "muse",
        "what chats do I have open?",
        "muse-chat-discovery",
        project_path=str(tmp_path),
    )

    assert res["success"] is True
    assert "Cuttle chat history" in seen["prompt"]
    assert "chat-history.md" in seen["prompt"] or "cuttle_auth.db" in seen["prompt"]
