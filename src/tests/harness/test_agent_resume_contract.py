"""Contract tests for chat → CLI session resume across every agent store.

Why this file exists (ERR-20260816-009): `save_cursor_resume_id` rejected the
numeric DB chat id that authenticated web chats pass, the caller swallowed the
exception, and every `/cursor` turn silently became a brand-new CLI session with
no memory of its own chat. Nothing failed loudly — model badges and run history
kept working — so the only symptom was an agent reconstructing context it should
have simply resumed.

Coverage here is deliberately layered:

* Store contract, fanned out over *discovered* stores, so a new agent's store
  inherits it: save → load → clear must survive every chat-id shape Cuttle
  passes, and a save must actually write.
* Argv end-to-end for the Cursor path: turn 1 stores the CLI session id, turn 2
  must launch with ``--resume <uuid>``. This is the layer the outage happened at.
* Failure visibility: a store that cannot persist must say so, never degrade to
  "no memory" in silence.
"""

from __future__ import annotations

import json
import os
import queue
from pathlib import Path

import pytest

# Every id shape that reaches a store in production: authenticated web chats pass
# the raw DB int, older/local paths pass strings, Discord passes the prefixed form.
CHAT_ID_SHAPES = [
    pytest.param(147, id="int-db-id"),
    pytest.param("147", id="digit-string"),
    pytest.param("db_session_147", id="db_session-prefixed"),
    pytest.param("st_4dc562a08d44", id="opaque-string"),
]

# UUID shape: the stricter stores (cursor, codex) validate it.
CLI_SESSION_ID = "e941f922-fd0f-466d-98d1-0a729e62b2bd"


def test_discovery_finds_every_known_resume_store(isolated_resume_stores):
    """Guard the guard: discovery going blind would silently drop all coverage."""
    names = {store["name"] for store in isolated_resume_stores}
    assert {"cursor", "codex", "muse", "opencode", "antigravity", "hermes"} <= names, (
        f"resume store discovery regressed; found only {sorted(names)}"
    )


@pytest.mark.parametrize("chat_id", CHAT_ID_SHAPES)
def test_resume_store_round_trips_every_chat_id_shape(
    resume_store, chat_id, isolated_resume_stores, tmp_path
):
    if resume_store.get("import_error") is not None:
        pytest.fail(
            f"{resume_store['name']} resume store failed to import: "
            f"{resume_store['import_error']}"
        )

    save = resume_store["save"]
    load = resume_store["load"]
    clear = resume_store["clear"]
    cwd = str(tmp_path)

    assert load(cwd, chat_id) is None, "fresh store should have no binding"

    save(cwd, chat_id, CLI_SESSION_ID)

    map_file = resume_store["module"]._map_file()
    assert map_file.is_file(), (
        f"{resume_store['name']}: save_{resume_store['name']}_resume_id wrote nothing "
        f"for chat id {chat_id!r} ({type(chat_id).__name__}) — the next turn will start "
        "a memory-less CLI session (ERR-20260816-009)"
    )
    assert CLI_SESSION_ID in map_file.read_text(encoding="utf-8")

    assert load(cwd, chat_id) == CLI_SESSION_ID, (
        f"{resume_store['name']}: chat id {chat_id!r} ({type(chat_id).__name__}) did not "
        "round-trip, so the agent would silently lose its conversation"
    )

    if clear is not None:
        clear(cwd, chat_id)
        assert load(cwd, chat_id) is None


def test_resume_store_ignores_missing_chat_id(resume_store, isolated_resume_stores, tmp_path):
    """No chat id → no binding, but never an exception (callers wrap saves in try/except)."""
    if resume_store.get("import_error") is not None:
        pytest.skip(f"{resume_store['name']} did not import")
    save, load = resume_store["save"], resume_store["load"]
    cwd = str(tmp_path)
    for empty in (None, "", "   "):
        save(cwd, empty, CLI_SESSION_ID)
        assert load(cwd, empty) is None


# --------------------------------------------------------------------------- #
# Cursor path, at the layer that actually broke: the CLI argv
# --------------------------------------------------------------------------- #


class _FakeCursorProc:
    """Minimal stand-in for the streaming `agent -p` subprocess."""

    def __init__(self, lines):
        self.stdout = iter(lines)
        self.stderr = iter(())
        self.returncode = None
        self.pid = 4242
        self._done = False

    def poll(self):
        return 0 if self._done else None

    def wait(self, timeout=None):
        self._done = True
        self.returncode = 0
        return 0

    def kill(self):
        return None


def _stream_json_lines(session_id: str, text: str = "Pong."):
    return [
        json.dumps(
            {"type": "system", "subtype": "init", "model": "auto", "session_id": session_id}
        )
        + "\n",
        json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
        )
        + "\n",
        json.dumps(
            {"type": "result", "result": text, "is_error": False, "session_id": session_id}
        )
        + "\n",
    ]


@pytest.fixture
def cursor_cli_spy(tmp_path, monkeypatch):
    """Fake Cursor CLI that records argv, with the real store on a temp map file."""
    from scripts.utilities import cursor_cli_tool as cx
    from scripts.utilities import cursor_cli_session_store as store

    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "cursor_session_map.json")
    monkeypatch.setattr(cx, "_resolve_cursor_agent_argv", lambda: ["agent.exe"])

    calls = []
    real_popen = cx.subprocess.Popen

    def _fake_popen(cmd, **kwargs):
        # Only stand in for the agent itself; git probes stay real.
        if not (cmd and str(cmd[0]) == "agent.exe"):
            return real_popen(cmd, **kwargs)
        calls.append(list(cmd))
        return _FakeCursorProc(_stream_json_lines(CLI_SESSION_ID))

    monkeypatch.setattr(cx.subprocess, "Popen", _fake_popen)

    def run_turn(prompt: str, chat_session_id, workspace: str):
        """Run one chat turn; return (reply, launch argv for that turn)."""
        calls.clear()
        reply = cx._cursor_agent_oneline_prompt(
            prompt,
            workspace=workspace,
            status_queue=queue.Queue(),
            chat_session_id=chat_session_id,
            timeout=30.0,
        )
        assert calls, "Cursor CLI was never launched"
        return reply, calls[0]

    return {"module": cx, "store": store, "calls": calls, "run_turn": run_turn}


def test_cursor_run_saves_resume_even_without_status_queue(cursor_cli_spy, tmp_path):
    """Non-SSE / no-queue callers must still use stream-json and persist --resume.

    Gating stream mode on ``status_queue is not None`` made every harness smoke and
    non-streaming chat turn memory-less: text mode never emits session_id.
    """
    tm = cursor_cli_spy["module"]
    store = cursor_cli_spy["store"]
    calls = cursor_cli_spy["calls"]
    chat_id = 147
    workspace = str(tmp_path)

    calls.clear()
    reply = tm._cursor_agent_oneline_prompt(
        "remember the word banana",
        workspace=workspace,
        status_queue=None,
        chat_session_id=chat_id,
        timeout=30.0,
    )
    assert reply and "Pong." in reply
    assert calls, "Cursor CLI was never launched"
    assert "stream-json" in calls[0], (
        "without a status_queue Cursor must still use stream-json so session_id "
        "is emitted and resume can be saved"
    )
    assert store.load_cursor_resume_id(workspace, chat_id) == CLI_SESSION_ID, (
        "turn 1 with status_queue=None did not persist resume — non-SSE chats "
        "would be memory-less"
    )

    calls.clear()
    tm._cursor_agent_oneline_prompt(
        "what word?",
        workspace=workspace,
        status_queue=None,
        chat_session_id=chat_id,
        timeout=30.0,
    )
    assert calls and "--resume" in calls[0]


def test_cursor_run_saves_resume_then_passes_it_on_the_next_turn(cursor_cli_spy, tmp_path):
    """The outage in one test: turn 2 of the same chat must launch with --resume.

    Chat id is the raw int an authenticated web chat sends. Before the fix, turn 1
    persisted nothing and turn 2 started a fresh session with no --resume.
    """
    store = cursor_cli_spy["store"]
    run_turn = cursor_cli_spy["run_turn"]
    chat_id = 147
    workspace = str(tmp_path)

    reply, first_argv = run_turn("remember the word banana", chat_id, workspace)
    assert reply and "Pong." in reply
    assert "--resume" not in first_argv, "a first turn has nothing to resume"

    assert store.load_cursor_resume_id(workspace, chat_id) == CLI_SESSION_ID, (
        "turn 1 did not persist the Cursor session id for an int chat id, so every "
        "later turn would be memory-less (ERR-20260816-009)"
    )

    _, second_argv = run_turn("what word did I ask you to remember?", chat_id, workspace)
    assert "--resume" in second_argv, (
        "turn 2 launched Cursor without --resume; the agent starts from scratch and "
        "will reconstruct context instead of remembering the chat"
    )
    assert second_argv[second_argv.index("--resume") + 1] == CLI_SESSION_ID


def test_cursor_run_does_not_resume_a_different_chat(cursor_cli_spy, tmp_path):
    """Resume bindings are per chat: chat B must not inherit chat A's CLI session."""
    run_turn = cursor_cli_spy["run_turn"]
    workspace = str(tmp_path)

    run_turn("seed chat A", 147, workspace)
    _, other_argv = run_turn("fresh chat B", 155, workspace)
    assert "--resume" not in other_argv, (
        "chat 155 resumed chat 147's Cursor session — cross-chat context leak"
    )


def test_contract_detects_an_int_hostile_store(cursor_cli_spy, tmp_path, monkeypatch, capsys):
    """Positive control: reintroduce the ERR-20260816-009 guard and prove we notice.

    Without this, the argv assertions above could pass for the wrong reason (e.g. a
    store that quietly accepts everything). Here the save is deliberately the pre-fix
    version, which raises on the int chat id an auth chat sends.
    """
    store = cursor_cli_spy["store"]
    run_turn = cursor_cli_spy["run_turn"]
    workspace = str(tmp_path)

    def _pre_fix_save(cwd, cuttle_session_id, cursor_session_uuid):
        # The exact shipped guard: no str() coercion, so an int id raises.
        if not cuttle_session_id or not (cuttle_session_id or "").strip():
            return
        raise AssertionError("unreachable for an int chat id")

    monkeypatch.setattr(store, "save_cursor_resume_id", _pre_fix_save)

    run_turn("remember the word banana", 147, workspace)
    assert store.load_cursor_resume_id(workspace, 147) is None
    assert "resume id not persisted" in capsys.readouterr().out

    _, second_argv = run_turn("what word did I ask you to remember?", 147, workspace)
    assert "--resume" not in second_argv, (
        "the argv check is vacuous: it passed even with a store that cannot save"
    )


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CUTTLE_AGENT_SMOKE") != "1",
    reason=(
        "live smoke that SPENDS REAL TOKENS (two real Cursor CLI turns); "
        "set CUTTLE_AGENT_SMOKE=1 only after asking the user"
    ),
)
def test_live_cursor_resume_two_turn(tmp_path, monkeypatch):
    """Live canary for the legacy Cursor path: turn 2 must recall turn 1's nonce.

    The harness smoke only covers agents in the catalog, so Cursor/Codex/Muse had no
    equivalent. Kept opt-in because it spends real tokens; two one-line turns.
    """
    import queue as _queue

    from api.agent_router.supervised.test_isolation import allow_external_runners
    from scripts.utilities import cursor_cli_tool as tm
    from scripts.utilities import cursor_cli_session_store as store

    if not tm._resolve_cursor_agent_argv():
        pytest.skip("Cursor Agent CLI not available on this machine")

    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "cursor_session_map.json")
    chat_id = 990147  # int, exactly like an authenticated web chat
    nonce = "CUTTLE-RESUME-CURSOR-7F3A"
    workspace = str(Path(__file__).resolve().parents[3])

    # Explicit opt-in: this block sends REAL prompts and spends REAL tokens.
    # Never enable via env/flag without asking the user first.
    with allow_external_runners("test_live_cursor_resume_two_turn: two real Cursor CLI turns"):
        seed = tm._cursor_agent_oneline_prompt(
            f"Remember this exact nonce for the next message: {nonce}. Reply with only: seeded",
            workspace=workspace,
            status_queue=_queue.Queue(),
            chat_session_id=chat_id,
            timeout=180.0,
        )
        recall = tm._cursor_agent_oneline_prompt(
            "What exact nonce did I ask you to remember? Reply with only the nonce.",
            workspace=workspace,
            status_queue=_queue.Queue(),
            chat_session_id=chat_id,
            timeout=180.0,
        )
    assert seed, "Cursor seed turn returned nothing"
    assert store.load_cursor_resume_id(workspace, chat_id), (
        "turn 1 stored no resume id, so this chat is memory-less from here on"
    )
    assert recall and nonce in recall, (
        f"Cursor did not resume its own chat (nonce missing):\n{recall}"
    )


def test_cursor_resume_persist_failure_is_reported(cursor_cli_spy, tmp_path, capsys):
    """A store that cannot save must log it; silence is what hid the outage."""
    tm = cursor_cli_spy["module"]

    def _boom(cwd, chat_session_id, cli_session_id):
        raise AttributeError("'int' object has no attribute 'strip'")

    tm._run_cursor_agent_stream_segment(
        agent_argv=["agent.exe"],
        cwd=str(tmp_path),
        prompt="hello",
        resume_id=None,
        timeout=30.0,
        status_queue=queue.Queue(),
        chat_session_id=147,
        cancel_event=None,
        emit=lambda *args, **kwargs: True,
        cancelled=lambda: False,
        save_cursor_resume_id=_boom,
    )

    out = capsys.readouterr().out
    assert "resume id not persisted" in out, (
        "a failed resume save must be logged, not swallowed — otherwise every later "
        "turn silently loses the conversation"
    )
