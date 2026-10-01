"""Why Muse reports "no traces of Cuttle chats", and the context that fixes it.

Muse Code runs inside WSL against `/mnt/e/...`. Three things combine to make it
conclude Cuttle has no chat history:

1. Transcripts are rows in a **binary, gitignored** SQLite file, so `rg`/`grep`
   over the working tree matches only *source* that mentions chats — which is
   exactly why the one time it "found" a chat, the hit was a test file.
2. The documented live-view API (`https://127.0.0.1:8080/api/shell/panes`)
   listens on the Windows host; from WSL `127.0.0.1` is the WSL VM.
3. Nothing in the `/muse` prompt ever told it where the transcripts live.

The environment-dependent facts (1 and 2) are asserted here as skip-if-absent
probes so they document the real machine; the prompt wiring (3) is a hard
regression test.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from api.cuttle_ui_capabilities import chat_store_path, cuttle_chat_store_addon
from scripts.utilities.muse_cli_tool import windows_to_wsl_path

REPO_ROOT = Path(__file__).resolve().parents[2]

# `sqlite3` writes this exact 16-byte magic at offset 0 of every database.
SQLITE_MAGIC = b"SQLite format 3\x00"


def _wsl_available() -> bool:
    return sys.platform == "win32" and bool(shutil.which("wsl"))


def _run_wsl(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl", "-e", "bash", "-lc", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# --------------------------------------------------------------------------
# Root cause 1 — transcripts are a gitignored binary blob, invisible to search
# --------------------------------------------------------------------------


def test_chat_store_is_a_real_sqlite_file_outside_git(monkeypatch):
    """The transcripts live in a binary file that git ignores.

    Code-search tools honour `.gitignore` and skip binaries, so an agent that
    greps the workspace cannot see chat content no matter how hard it looks.
    """
    import api.auth_db as auth_db

    # Read-only check of the live path (conftest redirects DB_PATH to tmp).
    monkeypatch.setattr(auth_db, "DB_PATH", auth_db.data_db_dir() / "cuttle_auth.db")
    db = chat_store_path()
    assert db, "chat store path must resolve for the prompt addon to be useful"
    db_path = Path(db)
    assert db_path.name == "cuttle_auth.db"

    ignored = subprocess.run(
        ["git", "check-ignore", "-q", str(db_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, (
        f"{db_path} is no longer gitignored — the search-invisibility premise "
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
        text=True,
        check=False,
    )
    hits = [line for line in found.stdout.splitlines() if line.strip()]
    assert hits, "expected source files that mention chat_messages"
    assert not any(h.endswith(".db") for h in hits), (
        "git grep reached a database file; the premise of this test is wrong"
    )


# --------------------------------------------------------------------------
# Root cause 2 — WSL cannot reach the Flask API, but can reach the file
# --------------------------------------------------------------------------


@pytest.mark.skipif(not _wsl_available(), reason="WSL not installed")
def test_wsl_sees_the_chat_db_on_the_mounted_drive():
    """The file itself is reachable from where Muse runs — path mapping is fine."""
    db = chat_store_path()
    if not db or not Path(db).exists():
        pytest.skip("no chat database on this machine yet")
    wsl_path = windows_to_wsl_path(str(Path(db).resolve()))
    assert wsl_path.startswith("/mnt/")

    probe = _run_wsl(f"test -r {wsl_path} && echo READABLE")
    assert "READABLE" in probe.stdout, (
        f"{wsl_path} not readable from WSL:\n{probe.stdout}\n{probe.stderr}"
    )


@pytest.mark.skipif(not _wsl_available(), reason="WSL not installed")
def test_documented_wsl_read_recipe_returns_real_messages():
    """The recipe the addon hands Muse must actually work from inside WSL.

    `sqlite3` (the CLI) is frequently missing in a WSL distro while python3's
    `sqlite3` module is always present, so the addon points at python3.
    """
    db = chat_store_path()
    if not db or not Path(db).exists():
        pytest.skip("no chat database on this machine yet")
    wsl_path = windows_to_wsl_path(str(Path(db).resolve()))

    script = (
        "python3 -c \"import sqlite3;"
        f"c=sqlite3.connect('file:{wsl_path}?mode=ro',uri=True);"
        "print('ROWS', c.execute('select count(*) from chat_messages').fetchone()[0])\""
    )
    out = _run_wsl(script)
    assert "ROWS" in out.stdout, (
        f"documented read recipe failed in WSL:\n{out.stdout}\n{out.stderr}"
    )
    assert int(out.stdout.split("ROWS", 1)[1].strip().split()[0]) >= 0


# --------------------------------------------------------------------------
# Root cause 3 — the /muse prompt never carried this context (regression)
# --------------------------------------------------------------------------


def test_chat_store_addon_names_the_db_and_warns_off_empty_searches():
    from pathlib import Path

    addon = cuttle_chat_store_addon(wsl=False)
    assert "cuttle_auth.db" in addon or "chat-history.md" in addon
    assert "chat-history.md" in addon
    assert "gitignored" in addon.lower() or "not in the working tree" in addon.lower()

    runbook = Path(__file__).resolve().parents[2] / ".cuttle_global" / "docs" / "chat-history.md"
    body = runbook.read_text(encoding="utf-8")
    assert "cuttle_auth.db" in body
    assert "chat_messages" in body
    assert "tasks.db" in body
    assert "no traces of Cuttle chats" in body
    assert "leading zeros" in body.lower()


def test_chat_store_addon_maps_the_path_and_drops_localhost_for_wsl():
    """A WSL agent given a `E:\\...` path is being misled — use the WSL path."""
    if sys.platform != "win32":
        pytest.skip("WSL path mapping is a Windows-host concern")
    from pathlib import Path

    addon = cuttle_chat_store_addon(wsl=True)
    db = chat_store_path()
    assert db

    expected = windows_to_wsl_path(str(Path(db)))
    assert expected in addon
    assert str(Path(db)) not in addon
    # Recipes / localhost firewall notes live in the runbook.
    runbook = Path(__file__).resolve().parents[2] / ".cuttle_global" / "docs" / "chat-history.md"
    body = runbook.read_text(encoding="utf-8")
    assert "WSL" in body
    assert "api.chat_cli" in body


def test_muse_prompt_carries_chat_context_when_the_user_asks_about_chats(monkeypatch):
    """Regression: `/muse` built its prompt with no pane map and no DB pointer.

    `_run_muse_web_command` only called `with_cuttle_ui_capabilities`, unlike the
    remote-agent path which prepends `_build_shell_pane_prompt_addon`.
    """
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod

    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "wsl", "path": "/x"})

    enriched = w._with_muse_chat_context("summarize my recent Cuttle chats")

    assert "Cuttle chat history" in enriched
    assert "cuttle_auth.db" in enriched
    assert enriched.endswith("summarize my recent Cuttle chats")


def test_muse_prompt_carries_chat_context_for_ch_session_handle(monkeypatch):
    """CH-000149 failure: 'summarize CH-000147' never matched the word-only regex."""
    if sys.platform != "win32":
        pytest.skip("Muse chat-store enrichment is WSL/Windows-host specific")
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod

    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "wsl", "path": "/x"})
    monkeypatch.setattr(w, "_build_shell_pane_prompt_addon", lambda _p: "")

    prompt = "Can you summarize CH-000147?"
    enriched = w._with_muse_chat_context(prompt)

    assert "Cuttle chat history" in enriched
    assert "cuttle_auth.db" in enriched
    assert "CH-000147" in enriched  # the user's own handle survives the prefix
    assert "tasks.db" in enriched
    assert enriched.endswith(prompt)


def test_muse_prompt_stays_lean_for_unrelated_work(monkeypatch):
    """Coding prompts must not pay for the chat-store block every turn."""
    from api import web_chat_api as w

    monkeypatch.setattr(w, "_build_shell_pane_prompt_addon", lambda _p: "")

    prompt = "refactor the retry loop in agent_process.py"
    assert w._with_muse_chat_context(prompt) == prompt


def test_muse_execution_sends_the_enriched_prompt_to_the_cli(tmp_path, monkeypatch):
    """End-to-end through `_run_muse_web_command`: the CLI must receive it."""
    from api import web_chat_api as w
    import scripts.utilities.muse_cli_tool as muse_mod
    from scripts.utilities import muse_cli_session_store as store
    from scripts.utilities.muse_cli_tool import MuseCliTool

    seen = {}

    async def fake_execute_prompt(self, prompt, *args, **kwargs):
        seen["prompt"] = prompt
        return {"success": True, "output": "ok", "usage": {}}

    monkeypatch.setattr(muse_mod, "muse_available", lambda: True)
    monkeypatch.setattr(muse_mod, "muse_resolution", lambda: {"mode": "wsl", "path": "/x"})
    monkeypatch.setattr(MuseCliTool, "execute_prompt", fake_execute_prompt)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)

    res = w._run_muse_web_command(
        "what chats do I have open?",
        "muse-chat-discovery",
        project_path=str(tmp_path),
    )

    assert res["success"] is True
    assert "Cuttle chat history" in seen["prompt"]
    assert "chat-history.md" in seen["prompt"] or "cuttle_auth.db" in seen["prompt"]
