"""Stop mid-turn → followup must resume the *same* CLI session (CH-000513).

Contract (all resume-capable harness agents):

1. Secret from the interrupted turn is remembered on followup.
2. Resume id does **not** change (no fresh CLI session).
3. Stop leaves **no** orphaned CLI/helper processes holding the writer lock.

CH-000513 failure mode: Stop killed the parent ``codex`` but
``codex-code-mode-host`` kept the thread-store writer → followup
``thread/resume failed: … already has an active writer``.

Live scope: ``CUTTLE_AGENT_SMOKE=1`` + ``CUTTLE_AGENT_SMOKE_SCOPE=stop_followup``
(3 prompts per agent: plant secret → long turn + Stop → recall).
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Set
from unittest.mock import MagicMock

import pytest

from api.agent_harness.catalog import get_agent, list_agents, reload_catalog
from api.agent_harness.smoke_policy import live_model, parametrize_ids, scope_enabled

SECRET = "jello"
RESUME_ID = "cli-resume-lock"


def setup_function(_fn=None):
    reload_catalog()


_STALE_WRITER_MARKERS = (
    "active writer",
    "thread-store conflict",
    "thread/resume failed",
    "already has an active writer",
    "session is locked",
    "resource busy",
    "another process is using",
)

_RESUME_AGENT_IDS = [
    aid
    for aid in (list_agents() or [])
    if (get_agent(aid) and get_agent(aid)[0].resume)
] or ["cursor", "codex", "muse"]


def _stub_kernel_side_channels(monkeypatch) -> None:
    monkeypatch.setattr(
        "api.query_tracker.start_query_tracking", lambda *a, **k: "qid"
    )
    monkeypatch.setattr(
        "api.query_tracker.finish_query_tracking", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "api.query_tracker.get_query_tracker", lambda *a, **k: None
    )
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.unregister_execution", lambda *a, **k: None)


def test_ch000513_active_writer_markers_are_detectable():
    raw = (
        "failed to initialize thread persistence: thread-store conflict: "
        "thread 01a0cd31-b9ee-7561-bc88-714f89b43073 already has an active writer\n"
        "Error: thread/resume: thread/resume failed: thread "
        "01a0cd31-b9ee-7561-bc88-714f89b43073 already has an active writer (code -32600)"
    )
    low = raw.lower()
    assert any(m in low for m in _STALE_WRITER_MARKERS)


def _install_locking_adapter(
    *,
    agent_id: str,
    monkeypatch,
    tmp_path,
    orphan_writer: bool,
):
    """Fake adapter: first turn holds a writer; followup resumes same sid + secret."""
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_map_file", lambda: tmp_path / f"last_agent_{agent_id}.json")
    _stub_kernel_side_channels(monkeypatch)

    delivery._busy.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    reg._runs.clear()

    store: Dict[str, Any] = {"sid": None, "secret": SECRET}
    writer_lock = threading.Event()
    started = threading.Event()
    cancel_seen = threading.Event()
    resumes: List[Optional[str]] = []
    live_pids: Set[int] = set()

    parent = MagicMock()
    parent.poll.return_value = None
    parent.pid = 424200
    parent.returncode = None
    orphan = MagicMock()
    orphan.poll.return_value = None
    orphan.pid = 424201
    orphan.returncode = None
    live_pids.add(424200)
    if orphan_writer:
        live_pids.add(424201)

    def _pid_alive(pid):
        try:
            return int(pid) in live_pids
        except (TypeError, ValueError):
            return False

    def _descendants(root):
        try:
            root_i = int(root)
        except (TypeError, ValueError):
            return []
        if orphan_writer and root_i == 424200 and 424201 in live_pids:
            return [424201]
        return []

    def _kill_pid(pid):
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            return False
        live_pids.discard(pid_i)
        if pid_i == 424200:
            parent.poll.return_value = 1
            parent.returncode = 1
        if pid_i == 424201:
            orphan.poll.return_value = 1
            orphan.returncode = 1
            writer_lock.clear()
        if not live_pids:
            writer_lock.clear()
        return True

    monkeypatch.setattr(reg, "pid_alive", _pid_alive)
    monkeypatch.setattr(reg, "list_descendant_pids", _descendants)
    monkeypatch.setattr(reg, "kill_pid_tree", _kill_pid)
    monkeypatch.setattr(reg, "wait_pid_dead", lambda pid, timeout=5.0: not _pid_alive(pid))

    real_kill_proc = reg.kill_process_tree

    def _kill_proc(proc):
        pid = getattr(proc, "pid", None)
        if pid:
            # Snapshot descendants first (mirrors production), then kill root + kids.
            kids = list(_descendants(pid))
            _kill_pid(pid)
            for kid in kids:
                _kill_pid(kid)
            return True
        return real_kill_proc(proc)

    monkeypatch.setattr(reg, "kill_process_tree", _kill_proc)
    monkeypatch.setattr(reg, "_kill_proc", _kill_proc)

    class _LockingAdapter:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return str(tmp_path)

        def load_resume(self, cwd, chat_session_id):
            return store["sid"]

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            store["sid"] = cli_session_id

        def clear_resume(self, cwd, chat_session_id):
            store["sid"] = None

        async def execute(self, prompt, **kwargs):
            resume = kwargs.get("resume")
            resumes.append(resume)
            cancel_event = kwargs.get("cancel_event")
            chat_session_id = kwargs.get("chat_session_id")
            text = str(prompt or "")

            if resume:
                if writer_lock.is_set():
                    err = (
                        "thread-store conflict: thread cli-resume-lock already has an "
                        "active writer\n"
                        "Error: thread/resume: thread/resume failed: thread "
                        "cli-resume-lock already has an active writer (code -32600)"
                    )
                    return AgentResult(success=False, output=err, error=err)
                # Same session — recall the secret planted on turn 1.
                assert resume == RESUME_ID
                return AgentResult(
                    success=True,
                    output=store["secret"],
                    session_id=RESUME_ID,
                )

            # Fresh session without resume would mint a new id — forbidden for this
            # contract after a Stop that already saved RESUME_ID.
            if store["sid"] == RESUME_ID:
                return AgentResult(
                    success=False,
                    output="ERROR: started a new session instead of resuming",
                    error="new session",
                )

            store["sid"] = RESUME_ID
            if "secret word is" in text.lower():
                # parse "secret word is jello"
                store["secret"] = SECRET
            writer_lock.set()
            if chat_session_id is not None:
                reg.attach_process(chat_session_id, parent)
                if orphan_writer:
                    reg.track_pid(chat_session_id, orphan.pid)
            started.set()

            deadline = time.time() + 5.0
            while time.time() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    cancel_seen.set()
                    break
                time.sleep(0.02)

            return AgentResult(
                success=False,
                output=f"[{agent_id} interrupted — cancelled]",
                error=f"{agent_id} cancelled",
                session_id=RESUME_ID,
                meta={"cancelled": True},
            )

    manifest = AgentManifest(
        id=agent_id,
        label=agent_id.title(),
        slash=f"/{agent_id}",
        resume=True,
        capabilities_inject="never",
    )
    adapter = _LockingAdapter()
    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, adapter)
    )
    monkeypatch.setattr(
        kernel,
        "_compile_agent_prompt",
        lambda *args, **kwargs: (
            (kwargs.get("prompt") if "prompt" in kwargs else args[1]),
            {},
        ),
    )

    chat_id = 880000 + (abs(hash(f"{agent_id}:{orphan_writer}")) % 1000)
    return {
        "store": store,
        "writer_lock": writer_lock,
        "started": started,
        "cancel_seen": cancel_seen,
        "resumes": resumes,
        "live_pids": live_pids,
        "chat_id": chat_id,
        "delivery": delivery,
        "reg": reg,
        "kernel": kernel,
    }


def _run_stop_then_followup(ctx, agent_id: str, tmp_path) -> str:
    delivery = ctx["delivery"]
    reg = ctx["reg"]
    kernel = ctx["kernel"]
    chat_id = ctx["chat_id"]

    assert delivery.try_begin(str(chat_id))

    def _run_turn1():
        kernel.run_agent_web_command(
            agent_id,
            f"Secret word is {SECRET}. Keep working for a long time.",
            chat_session_id=chat_id,
            project_path=str(tmp_path),
            timeout=30.0,
        )

    t = threading.Thread(target=_run_turn1, name=f"stop-followup-{agent_id}", daemon=True)
    t.start()
    assert ctx["started"].wait(timeout=3.0), f"{agent_id}: turn 1 never claimed the writer"
    assert ctx["store"]["sid"] == RESUME_ID

    info = reg.cancel_session_runs(chat_id)
    assert info.get("cancelled") is True
    assert info.get("orphans_cleared") is True, (
        f"{agent_id}: Stop left orphans alive: pids={ctx['live_pids']!r}"
    )
    assert not ctx["live_pids"], f"{agent_id}: orphan PIDs still live: {ctx['live_pids']}"
    assert not ctx["writer_lock"].is_set(), f"{agent_id}: writer lock still held after Stop"
    assert ctx["cancel_seen"].wait(timeout=3.0), f"{agent_id}: turn 1 did not observe cancel"
    t.join(timeout=5.0)
    assert not t.is_alive(), f"{agent_id}: turn 1 thread hung after cancel"

    delivery.end(str(chat_id))
    assert delivery.try_begin(str(chat_id))

    follow = kernel.run_agent_web_command(
        agent_id,
        "What was the secret word? Reply with only the word.",
        chat_session_id=chat_id,
        project_path=str(tmp_path),
        timeout=30.0,
    )
    delivery.end(str(chat_id))
    return (follow.get("response") or "").strip()


@pytest.mark.parametrize("agent_id", _RESUME_AGENT_IDS)
@pytest.mark.parametrize("orphan_writer", [False, True], ids=["clean-kill", "orphan-helper"])
def test_kernel_stop_then_followup_same_session_remembers_secret(
    agent_id, orphan_writer, monkeypatch, tmp_path
):
    """Stop → followup: same resume id, secret recalled, no orphans."""
    ctx = _install_locking_adapter(
        agent_id=agent_id,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        orphan_writer=orphan_writer,
    )
    sid_before = RESUME_ID
    resp = _run_stop_then_followup(ctx, agent_id, tmp_path)
    low = resp.lower()
    hit = [m for m in _STALE_WRITER_MARKERS if m in low]
    assert not hit, (
        f"{agent_id} followup after Stop hit stale writer (CH-000513):\n{resp}"
    )
    assert SECRET in low, (
        f"{agent_id} did not remember secret after Stop (same-session required):\n{resp}"
    )
    assert ctx["store"]["sid"] == sid_before, (
        f"{agent_id} minted a new CLI session after Stop: "
        f"{ctx['store']['sid']!r} (want {sid_before!r})"
    )
    # Followup execute must have been called with the same resume id (not None).
    assert any(r == RESUME_ID for r in ctx["resumes"][1:]), (
        f"{agent_id} followup did not resume {RESUME_ID}: resumes={ctx['resumes']!r}"
    )
    assert ctx["resumes"][-1] == RESUME_ID, (
        f"{agent_id} last execute was not a same-session resume: {ctx['resumes']!r}"
    )


def test_begin_run_kills_prior_proc_before_followup_resume(monkeypatch):
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg

    delivery._busy.clear()
    delivery._last_turn.clear()
    delivery._cancel_sticky.clear()
    reg._runs.clear()

    assert delivery.try_begin("513")
    live = MagicMock()
    live.poll.return_value = None
    live.pid = 513001
    live.returncode = None
    reg.begin_run("513", query_id="q-old")
    reg.attach_process("513", live)

    killed: List[Any] = []

    def _fake_kill_pid(pid):
        killed.append(pid)
        live.poll.return_value = 1
        live.returncode = 1
        return True

    monkeypatch.setattr(reg, "kill_pid_tree", _fake_kill_pid)
    monkeypatch.setattr(reg, "pid_alive", lambda pid: False)
    monkeypatch.setattr(reg, "list_descendant_pids", lambda pid: [])
    monkeypatch.setattr(reg, "wait_pid_dead", lambda pid, timeout=5.0: True)

    def _fake_kill_proc(proc):
        killed.append(proc)
        proc.poll.return_value = 1
        proc.returncode = 1
        return True

    monkeypatch.setattr(reg, "_kill_proc", _fake_kill_proc)
    monkeypatch.setattr(reg, "kill_process_tree", _fake_kill_proc)

    info = reg.cancel_session_runs("513")
    assert info.get("cancelled") is True
    assert info.get("orphans_cleared") is True

    assert delivery.try_begin("513")
    ev = reg.begin_run("513", query_id="q-new")
    assert ev.is_set() is False
    assert not reg.has_live_process("513")
    delivery.end("513")
    reg.end_run("513", query_id="q-new")


# --------------------------------------------------------------------------- #
# Live smoke: seed (Stop) → recall secret on same session; no orphans
# --------------------------------------------------------------------------- #

_LIVE_IDS = parametrize_ids(list_agents() or ["cursor"])
_LIVE_ERROR_MARKERS = (
    "❌",
    "not found",
    "auth failed",
    "not authenticated",
    "quota",
    "failed (exit",
    "not supported when using",
) + _STALE_WRITER_MARKERS

# Process name fragments that must not remain from *our* tracked PIDs after Stop.
_AGENT_HELPER_NAMES = (
    "codex",
    "codex-code-mode-host",
    "codex-computer-use",
    "claude",
    "muse",
    "hermes",
    "opencode",
    "agent",
    "dsh",
)


def _prepare_and_model(agent_id: str, session_id: int, manifest) -> str:
    mid = live_model(agent_id, manifest) or (manifest.default_model or None)
    if agent_id == "muse" and mid:
        from scripts.utilities.muse_cli_session_store import save_muse_model

        save_muse_model(str(session_id), mid)
    if agent_id == "codex" and mid:
        from scripts.utilities.codex_cli_session_store import (
            save_codex_effort,
            save_codex_model,
        )

        save_codex_model(str(session_id), mid)
        save_codex_effort(str(session_id), "low")
    return mid or ""


def _live_execute_kwargs(agent_id: str) -> dict:
    if agent_id == "codex":
        return {"reasoning_effort": "low"}
    return {}


def _pids_still_alive(pids) -> List[int]:
    from api.chat_run_registry import pid_alive

    return [int(p) for p in pids if pid_alive(p)]


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CUTTLE_AGENT_SMOKE") != "1",
    reason=(
        "LIVE smoke that SPENDS REAL TOKENS; set CUTTLE_AGENT_SMOKE=1 and "
        "CUTTLE_AGENT_SMOKE_SCOPE=stop_followup (3 prompts/agent, cheap smoke_model)"
    ),
)
@pytest.mark.skipif(
    not scope_enabled("stop_followup"),
    reason="CUTTLE_AGENT_SMOKE_SCOPE excludes stop_followup",
)
@pytest.mark.parametrize("agent_id", _LIVE_IDS)
def test_live_stop_then_followup_same_session(agent_id):
    """Live proof: plant secret → Stop mid-turn → same resume recalls secret; no orphans.

    Three prompts (required so agents that only persist resume at turn-end still
    have a session id before Stop, and the secret is committed to that session):

    1. Plant secret, await ``seeded``
    2. Long busywork turn → Stop → assert tracked PIDs are dead, resume unchanged
    3. Ask for the secret → must answer ``jello`` on the *same* resume id
    """
    from api import chat_delivery as delivery
    from api import chat_run_registry as reg
    from api.agent_harness.kernel import run_agent_web_command
    from api.agent_router.supervised.test_isolation import allow_external_runners

    pair = get_agent(agent_id)
    assert pair is not None
    manifest, adapter = pair
    if not adapter.available():
        pytest.skip(f"{agent_id} CLI not available on this machine")
    if not manifest.resume:
        pytest.skip(f"{agent_id} does not declare resume support")

    session_id = 991500 + (abs(hash(agent_id)) % 1000)
    expected = _prepare_and_model(agent_id, session_id, manifest)
    cwd = adapter.resolve_cwd(".")
    try:
        adapter.clear_resume(cwd, str(session_id))
    except Exception:
        pass

    delivery._busy.pop(str(session_id), None)
    delivery._last_turn.pop(str(session_id), None)
    delivery._cancel_sticky.pop(str(session_id), None)
    for k in list(reg._runs):
        if str(session_id) in str(k):
            reg._runs.pop(k, None)

    def _begin():
        assert delivery.try_begin(str(session_id))

    def _end():
        delivery.end(str(session_id))

    # --- Prompt 1: plant secret on a completed turn (commits resume + memory) ---
    _begin()
    with allow_external_runners(
        f"test_live_stop_then_followup_same_session[{agent_id}]: plant secret"
    ):
        plant = run_agent_web_command(
            agent_id,
            (
                f"Secret word is {SECRET}. "
                "Reply with exactly the word: seeded"
            ),
            chat_session_id=session_id,
            timeout=180.0,
            model_override=expected or None,
            execute_kwargs=_live_execute_kwargs(agent_id) or None,
        )
    _end()

    plant_resp = (plant.get("response") or "").strip().lower()
    assert plant_resp, f"{agent_id} plant turn returned empty"
    if "activity timeout" in plant_resp or "idle for" in plant_resp:
        pytest.skip(
            f"{agent_id} CLI hung on plant turn (idle timeout) — not a Stop/resume regression"
        )
    if any(m in plant_resp for m in ("not authenticated", "auth failed", "login")):
        pytest.skip(f"{agent_id} not authenticated — cannot prove Stop/resume")
    assert not any(m in plant_resp for m in _STALE_WRITER_MARKERS), (
        f"{agent_id} plant turn hit writer lock:\n{plant_resp}"
    )
    assert "seeded" in re.sub(r"<think>[\s\S]*?</think>", "", plant_resp, flags=re.I), (
        f"{agent_id} plant turn did not acknowledge seeded:\n{plant.get('response')}"
    )
    resume_before = adapter.load_resume(cwd, str(session_id))
    if not resume_before:
        pytest.skip(
            f"{agent_id} plant turn produced no resume id — cannot prove same-session Stop"
        )

    # --- Prompt 2: long turn → Stop → no orphans, resume unchanged ---
    _begin()
    turn2: Dict[str, Any] = {}

    def _busy():
        with allow_external_runners(
            f"test_live_stop_then_followup_same_session[{agent_id}]: busy (will Stop)"
        ):
            turn2["result"] = run_agent_web_command(
                agent_id,
                (
                    "Ignore the secret. Keep working: slowly list integers from 1 "
                    "to 500 with a short note on each. Do not finish early."
                ),
                chat_session_id=session_id,
                timeout=120.0,
                model_override=expected or None,
                execute_kwargs=_live_execute_kwargs(agent_id) or None,
            )

    t = threading.Thread(target=_busy, name=f"live-stop-{agent_id}", daemon=True)
    t.start()

    tracked: List[int] = []
    deadline = time.time() + 40.0
    while time.time() < deadline:
        tracked = reg.session_tracked_pids(session_id)
        if tracked:
            break
        time.sleep(0.2)

    assert tracked, (
        f"{agent_id}: busy turn never registered PIDs — cannot prove orphan cleanup"
    )
    # Let the resumed CLI actually start work before Stop.
    time.sleep(2.0)
    tracked = list(set(tracked) | set(reg.session_tracked_pids(session_id)))

    cancel_info = reg.cancel_session_runs(session_id)
    assert cancel_info.get("cancelled") is True
    assert cancel_info.get("orphans_cleared") is True, (
        f"{agent_id}: Stop did not clear orphans: {cancel_info}"
    )
    leftover = _pids_still_alive(tracked)
    assert not leftover, (
        f"{agent_id}: Stop left orphaned PIDs alive: {leftover} (tracked={tracked})"
    )
    assert not reg.has_live_process(session_id), (
        f"{agent_id}: registry still reports a live process after Stop"
    )

    t.join(timeout=90.0)
    assert not t.is_alive(), f"{agent_id}: busy thread hung after Stop"
    _end()

    resume_after_stop = adapter.load_resume(cwd, str(session_id))
    assert resume_after_stop == resume_before, (
        f"{agent_id}: resume id changed/cleared on Stop "
        f"({resume_before!r} → {resume_after_stop!r})"
    )

    # --- Prompt 3: recall secret on the same CLI session ---
    _begin()
    with allow_external_runners(
        f"test_live_stop_then_followup_same_session[{agent_id}]: recall secret"
    ):
        follow = run_agent_web_command(
            agent_id,
            "What was the secret word I told you? Reply with only that single word.",
            chat_session_id=session_id,
            timeout=120.0,
            model_override=expected or None,
            execute_kwargs=_live_execute_kwargs(agent_id) or None,
        )
    _end()

    resume_after = adapter.load_resume(cwd, str(session_id))
    assert resume_after == resume_before, (
        f"{agent_id}: followup minted a new CLI session "
        f"({resume_before!r} → {resume_after!r})"
    )

    resp = (follow.get("response") or "").strip()
    assert resp, f"{agent_id} recall after Stop returned empty"
    low = resp.lower()
    stale = [m for m in _STALE_WRITER_MARKERS if m in low]
    assert not stale, (
        f"{agent_id} recall after Stop hit stale writer (CH-000513):\n{resp}"
    )
    assert not any(m in low for m in _LIVE_ERROR_MARKERS if m not in _STALE_WRITER_MARKERS), (
        f"{agent_id} recall after Stop returned an error banner:\n{resp}"
    )
    visible = re.sub(r"<think>[\s\S]*?</think>", "", low, flags=re.I).strip()
    visible = re.sub(r"\s+", " ", visible)
    assert SECRET in visible, (
        f"{agent_id} did not remember secret `{SECRET}` on same-session followup:\n{resp}"
    )

    leftover_follow = _pids_still_alive(tracked)
    assert not leftover_follow, (
        f"{agent_id}: busy-tree PIDs still alive after recall: {leftover_follow}"
    )
