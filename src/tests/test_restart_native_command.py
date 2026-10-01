"""`/restart` must be a native Cuttle control command, never an agent turn.

Covers palette registration, dispatch order, self-exclusion from active work,
and busy-session/executing-job deduplication. No live Flask/daemon restart and
no model API calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAT_JS = REPO_ROOT / "src" / "web" / "js" / "chat_page.js"
SLASH_JS = REPO_ROOT / "src" / "web" / "js" / "chat_slash.js"
WEB_API = REPO_ROOT / "src" / "api" / "web_chat_api.py"


@pytest.fixture
def restart_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "api.flask_restart.STATUS_PATH", tmp_path / "cuttle_flask_restart_status.json"
    )
    monkeypatch.setattr(
        "api.flask_restart.REQUEST_PATH", tmp_path / "cuttle_flask_restart_request.json"
    )
    monkeypatch.setattr(
        "api.flask_restart.EVENTS_PATH", tmp_path / "flask_restart_events.jsonl"
    )
    monkeypatch.setattr("api.flask_restart.PROJECT_ROOT", tmp_path)
    yield tmp_path


@pytest.fixture
def fake_work(monkeypatch):
    """Control what chat_delivery / chat_run_registry / active_executions report."""
    from api import chat_delivery, chat_run_registry

    state = {"busy": [], "live": set(), "jobs": []}

    monkeypatch.setattr(chat_delivery, "busy_entries", lambda: list(state["busy"]))
    monkeypatch.setattr(
        chat_run_registry,
        "has_live_process",
        lambda sid: str(sid) in state["live"],
    )
    monkeypatch.setattr(
        chat_run_registry,
        "live_query_ids",
        lambda sid: [
            e["query_id"]
            for e in state["busy"]
            if str(e["session_id"]) == str(sid) and e.get("query_id")
        ],
    )
    import api.active_executions as ae

    monkeypatch.setattr(ae, "get_executing_jobs", lambda: list(state["jobs"]))
    return state


# ── 1. palette registration ──────────────────────────────────────────────────

def test_restart_is_registered_in_the_shared_slash_command_list():
    src = CHAT_JS.read_text(encoding="utf-8")
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    block = slash_src.split("const SLASH_COMMANDS = [", 1)[1].split("\n];", 1)[0]
    assert "prefix: '/restart '" in block
    assert "controlCommand: true" in block
    assert "Restart Flask" in block


def test_restart_subcommands_are_searchable_palette_entries():
    src = CHAT_JS.read_text(encoding="utf-8")
    assert "function buildRestartPaletteItems" in src
    for cmd in ("/restart status", "/restart graceful", "/restart when-idle", "/restart force --yes"):
        assert f"'{cmd}'" in src
    # Wired into the one palette builder every other command source feeds.
    assert "buildRestartPaletteItems(f)" in src
    assert ".concat(restartCmds)" in src


# ── 12/13. chip rendering + sticky agents survive ────────────────────────────

def test_control_command_uses_the_existing_chip_mechanism_and_keeps_sticky_agent():
    src = CHAT_JS.read_text(encoding="utf-8")
    # Palette entries are category 'command', i.e. the normal chip/badge path.
    assert "category: 'command'," in src
    # Sticky agent chips are not prepended onto a control command...
    compose = src.split("function composeMessageWithSlashChip", 1)[1].split("\n    }", 1)[0]
    assert "isControlCommandPrefix" in compose
    assert "isNativeControlCommand(body)" in compose
    # ...and are not evicted by sending one.
    sticky = src.split("function applyStickySlashAfterComposerSend", 1)[1].split("\n    }", 1)[0]
    assert "isNativeControlCommand(message)" in sticky


# ── 2/5/6. intercepted before sticky-agent prefixing ─────────────────────────

@pytest.mark.parametrize(
    "message",
    [
        "/restart status",
        "/cursor /restart status",  # sticky Cursor Auto composer
        "/codex /restart graceful",  # starred Codex command initialised session
        "/CURSOR /restart force --yes",
    ],
)
def test_sticky_agent_prefix_does_not_hide_the_control_command(message):
    from api.flask_restart import parse_restart_slash

    assert parse_restart_slash(message) is not None


def test_agent_prompts_that_merely_mention_restart_are_not_intercepted():
    from api.flask_restart import parse_restart_slash

    assert parse_restart_slash("/cursor please restart flask") is None
    assert parse_restart_slash("/restarting the build") is None
    assert parse_restart_slash("how do I /restart") is None


def test_restart_is_dispatched_before_router_and_agent_branches():
    src = WEB_API.read_text(encoding="utf-8")
    chat_route = src.split("def chat_endpoint", 1)[-1] if "def chat_endpoint" in src else src

    restart_at = src.index("from api.flask_restart import (")
    router_at = src.index("from api.agent_router.commands import (")
    assert restart_at < router_at, "/restart must be handled before the router"

    # Pipeline/Discord entrypoint: /restart wins over the Cursor Agent CLI harness
    # (`/cursor` → `agent -p`). The old combined `/cursor(-cli)?` regex is gone.
    native_at = src.index("# Native Cuttle control command — must win over sticky agent prefixes.")
    cursor_cli_at = src.index(
        "# Bundled agents (incl. legacy /cursor-cli → /cursor) go through the harness only."
    )
    assert native_at < cursor_cli_at
    assert chat_route  # sanity: file parsed as expected


# ── 3/4. no agent, no router, no job ─────────────────────────────────────────

def test_status_makes_no_agent_router_or_job_calls(restart_paths, fake_work, monkeypatch):
    from api import flask_restart as fr

    calls = []

    import api.active_executions as ae

    monkeypatch.setattr(
        ae, "register_execution", lambda *a, **k: calls.append("register_execution")
    )

    from api import chat_delivery

    monkeypatch.setattr(
        chat_delivery, "try_begin", lambda *a, **k: calls.append("try_begin") or True
    )

    result = fr.handle_restart_slash("/restart status", session_id="77")
    assert result is not None
    assert result["type"] == "flask_restart"
    assert "Flask restart status" in result["response"]
    assert calls == []
    # Status is read-only: no daemon handoff written.
    assert not (restart_paths / "cuttle_flask_restart_request.json").exists()


# ── 7/8/9. graceful excludes its own control request ─────────────────────────

def test_graceful_excludes_its_own_control_request(restart_paths, fake_work, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    # Stale bookkeeping for the very chat issuing /restart, with no live process.
    fake_work["busy"] = [{"session_id": "42", "query_id": "q-self"}]
    fake_work["jobs"] = [{"query_id": "q-self", "pipeline_name": "Cuttle_Main"}]

    work = fr.list_active_work(exclude_session_id="42")
    assert work["is_idle"] is True
    assert work["active_count"] == 0

    result = fr.request_restart(mode="graceful", session_id="42", user_source="chat")
    assert result["success"] is True
    assert result["restart_scheduled"] is True


def test_graceful_succeeds_when_no_unrelated_work(restart_paths, fake_work, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    result = fr.request_restart(mode="graceful", session_id="42")
    assert result["success"] is True
    assert (restart_paths / "cuttle_flask_restart_request.json").exists()


def test_graceful_rejects_when_a_separate_task_is_active(restart_paths, fake_work, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    fake_work["busy"] = [{"session_id": "99", "query_id": "q-other"}]
    fake_work["live"] = {"99"}

    result = fr.request_restart(mode="graceful", session_id="42")
    assert result["success"] is False
    assert result["state"] == "rejected"
    assert result["active_work"]["active_count"] == 1


def test_restart_slash_returns_locked_card_not_ack_bubbles(
    restart_paths, fake_work, monkeypatch
):
    from api import flask_restart as fr

    persisted = []
    monkeypatch.setattr(
        fr,
        "_persist_session_message",
        lambda session_id, text, **kw: persisted.append((session_id, text)) or True,
    )
    result = fr.handle_restart_slash("/restart graceful", session_id="42")
    assert result is not None
    assert "cuttle_action_form" in result["response"]
    assert "Flask restart acknowledged" not in result["response"]
    assert result["restart_id"] in result["response"]
    assert persisted == []
    assert (restart_paths / "cuttle_flask_restart_request.json").exists()


def test_restart_slash_rejected_shows_card_without_persisting_ack(
    restart_paths, fake_work, monkeypatch
):
    from api import flask_restart as fr

    persisted = []
    monkeypatch.setattr(
        fr,
        "_persist_session_message",
        lambda session_id, text, **kw: persisted.append(text) or True,
    )
    fake_work["busy"] = [{"session_id": "99", "query_id": "q-other"}]
    fake_work["live"] = {"99"}
    result = fr.handle_restart_slash("/restart graceful", session_id="42")
    assert result["success"] is False
    assert "cuttle_action_form" in result["response"]
    assert "postponed" in result["response"].lower()
    assert persisted == []


def test_restart_slash_status_stays_plain_text(restart_paths, fake_work):
    from api import flask_restart as fr

    result = fr.handle_restart_slash("/restart status", session_id="77")
    assert "cuttle_action_form" not in result["response"]
    assert "Flask restart status" in result["response"]


def test_live_agent_run_in_the_same_chat_still_blocks(restart_paths, fake_work, monkeypatch):
    """Self-exclusion must not hide a genuine subprocess in the same session."""
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    fake_work["busy"] = [{"session_id": "42", "query_id": "q-real"}]
    fake_work["live"] = {"42"}

    result = fr.request_restart(mode="graceful", session_id="42")
    assert result["success"] is False
    assert result["state"] == "rejected"


# ── 10. when-idle can actually reach idle ────────────────────────────────────

def test_when_idle_reaches_idle_despite_its_own_request(restart_paths, fake_work, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    monkeypatch.setattr(fr, "_ensure_when_idle_watcher", lambda: None)

    fake_work["busy"] = [{"session_id": "99", "query_id": "q-other"}]
    fake_work["live"] = {"99"}
    scheduled = fr.request_restart(mode="when-idle", session_id="42")
    assert scheduled["state"] == "waiting_for_idle"
    assert fr.maybe_fire_when_idle() is None

    # Other work finishes; the scheduling chat's own record must not block.
    fake_work["busy"] = [{"session_id": "42", "query_id": "q-self"}]
    fake_work["live"] = set()
    fired = fr.maybe_fire_when_idle()
    assert fired is not None
    assert fr.read_status()["state"] in ("preparing", "acknowledged")


# ── 11. deduplication ────────────────────────────────────────────────────────

def test_one_agent_turn_is_not_counted_as_both_busy_session_and_job(fake_work):
    from api import flask_restart as fr

    fake_work["busy"] = [{"session_id": "7", "query_id": "q1"}]
    fake_work["live"] = {"7"}
    fake_work["jobs"] = [{"query_id": "q1", "pipeline_name": "Cuttle_Main"}]

    work = fr.list_active_work()
    assert work["active_count"] == 1
    assert work["deduped_job_count"] == 1
    assert len(work["tasks"]) == 1
    task = work["tasks"][0]
    assert task["session_id"] == "7"
    assert task["query_id"] == "q1"
    # Identifiers from the job record are retained.
    assert task["pipeline_name"] == "Cuttle_Main"
    # Raw records still available for diagnostics.
    assert work["executing_jobs"]


def test_ch_handle_live_process_is_not_double_counted(fake_work, monkeypatch):
    """Busy uses bare ids; registry used to emit CH-000… and restart counted 2."""
    from api import chat_run_registry, flask_restart as fr

    fake_work["busy"] = [{"session_id": "478", "query_id": "q-muse"}]
    fake_work["live"] = {"478", "CH-000478"}
    fake_work["jobs"] = [{"query_id": "q-muse", "pipeline_name": "Muse Code"}]
    monkeypatch.setattr(
        chat_run_registry,
        "active_run_session_ids",
        # Simulate the pre-fix registry shape that leaked CH- aliases.
        lambda: ["CH-000478"],
    )
    monkeypatch.setattr(
        chat_run_registry,
        "live_query_ids",
        lambda sid: ["q-muse"] if str(sid) in ("478", "CH-000478") else [],
    )

    work = fr.list_active_work()
    assert work["active_count"] == 1
    assert len(work["tasks"]) == 1
    assert work["tasks"][0]["session_id"] == "478"
    assert work["tasks"][0]["kind"] == "chat_run"


def test_active_run_session_ids_collapses_ch_aliases():
    from api import chat_run_registry as crr

    crr._runs.clear()
    try:
        cancel = crr.begin_run("CH-000478", query_id="q1")
        assert cancel is not None

        class _Alive:
            def poll(self):
                return None

        crr.attach_process("CH-000478", _Alive())
        ids = crr.active_run_session_ids()
        assert ids == ["478"]
    finally:
        crr._runs.clear()


def test_genuinely_separate_work_is_not_collapsed(fake_work):
    from api import flask_restart as fr

    fake_work["busy"] = [{"session_id": "7", "query_id": "q1"}]
    fake_work["live"] = {"7"}
    fake_work["jobs"] = [
        {"query_id": "q1", "pipeline_name": "Cuttle_Main"},
        {"query_id": "q2", "pipeline_name": "Self_Improvement"},
    ]

    work = fr.list_active_work()
    assert work["active_count"] == 2
    kinds = sorted(t["kind"] for t in work["tasks"])
    assert kinds == ["chat_run", "job"]


def test_force_interrupt_list_is_deduplicated(restart_paths, fake_work, monkeypatch):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    fake_work["busy"] = [{"session_id": "7", "query_id": "q1"}]
    fake_work["live"] = {"7"}
    fake_work["jobs"] = [{"query_id": "q1", "pipeline_name": "Cuttle_Main"}]

    result = fr.request_restart(mode="force", session_id="42", force_confirm=True)
    assert result["success"] is True
    interrupted = fr.read_status()["interrupted_tasks"]
    assert len(interrupted) == 1
    assert interrupted[0]["session_id"] == "7"
    assert interrupted[0]["query_id"] == "q1"


def test_force_does_not_count_the_control_request_as_interrupted(
    restart_paths, fake_work, monkeypatch
):
    from api import flask_restart as fr

    monkeypatch.setattr(fr, "_persist_session_message", lambda *a, **k: True)
    fake_work["busy"] = [{"session_id": "42", "query_id": "q-self"}]

    result = fr.request_restart(mode="force", session_id="42", force_confirm=True)
    assert result["success"] is True
    assert fr.read_status()["interrupted_tasks"] == []


# ── 13. existing behaviour intact ────────────────────────────────────────────

def test_existing_slash_commands_and_sticky_prefixes_still_work():
    from api.flask_restart import parse_restart_slash
    from api.starred_slash import apply_default_sticky_prefix, sticky_prefix_from_text

    assert parse_restart_slash("/help") is None
    assert parse_restart_slash("/router status") is None
    assert parse_restart_slash("/pipelines") is None
    assert sticky_prefix_from_text("/cursor do a thing") == "/cursor "
    # Plain messages still get the sticky prefix; slash commands are left alone.
    assert apply_default_sticky_prefix("/restart status", None) == "/restart status"


def test_palette_still_lists_the_other_control_commands():
    src = CHAT_JS.read_text(encoding="utf-8")
    slash_src = SLASH_JS.read_text(encoding="utf-8")
    block = slash_src.split("const SLASH_COMMANDS = [", 1)[1].split("\n];", 1)[0]
    for prefix in ("/cursor ", "/codex ", "/help"):
        assert f"prefix: '{prefix}'" in block
    # Agent entries stay sticky; the harness adds new ones over time, so assert
    # the known agents rather than a count that ages out.
    for prefix in ("/claude ", "/hermes ", "/cursor ", "/codex ", "/muse ", "/deepseek ", "/antigravity "):
        entry = block.split(f"prefix: '{prefix}'", 1)[1].split("},", 1)[0]
        assert "stickySession: true" in entry, f"{prefix} lost its sticky flag"
