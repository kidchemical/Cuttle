"""Resume-delta delivery + GLOBAL.ini conformance (context policy audit).

Owner under test: ``src/api/cuttle_brain/context_delta.py`` (snapshot +
delta decisions) via its only caller,
``src/api/agent_harness/kernel.py`` (prepare-before / ack-after delivery).

The kernel tests drive ``run_agent_web_command`` with explicit fake adapters:
prepare the exact snapshot before execution, acknowledge that captured
version only after successful non-meta adapter execution, never recompute.
All store/state fakes: no live database, no repo config changes, no vendor
calls.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from api.cuttle_brain import context_delta as cd

SAFETY = "SAFETY CORE force-kill never another project"

FULL_PREAMBLE = "This <cuttle_context> block is Cuttle system context"
DELTA_PREAMBLE = "Context delta (not the user speaking)"


@pytest.fixture
def fake_stores(tmp_path, monkeypatch):
    """Redirect snapshot + handoff maps to throwaway files (never repo state)."""
    monkeypatch.setattr(cd, "_map_file", lambda: tmp_path / "snapshots.json")
    from api.cuttle_brain import handoff as ho

    monkeypatch.setattr(ho, "_map_file", lambda: tmp_path / "last_agent.json")
    return tmp_path


@pytest.fixture
def fake_globals(monkeypatch):
    """Controllable global rules behind context_delta's module-global lookup."""
    state = {"rules": [("00-safety.md", SAFETY), ("00-core.md", "GLOBAL CORE v1")]}

    def _load():
        return list(state["rules"])

    monkeypatch.setattr(cd, "load_global_rules", _load)
    return state


def _guest(tmp_path: Path, name: str = "guest", router_ini: str | None = None) -> Path:
    guest = tmp_path / name
    rules = guest / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    (rules / "01-a.md").write_text("Rule A", encoding="utf-8")
    if router_ini is not None:
        (guest / ".cuttle" / "GLOBAL.ini").write_text(router_ini, encoding="utf-8")
    return guest


def _manifest(agent_id: str = "cursor"):
    from api.agent_harness.types import AgentManifest

    return AgentManifest(
        id=agent_id,
        label=agent_id.title(),
        slash=f"/{agent_id}",
        resume=True,
        capabilities_inject="never",
    )


class _FakeAdapter:
    """Explicit fake executor: capture prompts, optional mid-turn hook."""

    def __init__(self, *, resume_id=None, succeed=True, on_execute=None, meta=None):
        self._resume = resume_id
        self._succeed = succeed
        self._on_execute = on_execute
        self._meta = meta
        self.prompts: list[str] = []
        self.executions = 0

    def available(self):
        return True

    def resolve_cwd(self, project_path):
        return project_path or "."

    def load_resume(self, cwd, chat_session_id):
        return self._resume

    def save_resume(self, cwd, chat_session_id, cli_session_id):
        pass

    def clear_resume(self, cwd, chat_session_id):
        pass

    def handle_meta(self, prompt, chat_session_id=None, **kwargs):
        return self._meta

    async def execute(
        self,
        prompt,
        *,
        cwd,
        resume,
        model,
        status_queue=None,
        chat_session_id=None,
        timeout=600.0,
    ):
        from api.agent_harness.types import AgentResult

        self.prompts.append(prompt)
        self.executions += 1
        if self._on_execute is not None:
            self._on_execute()
        if self._succeed:
            return AgentResult(success=True, output="ok", session_id="cli-1")
        return AgentResult(success=False, error="boom")


def _run(monkeypatch, manifest, adapter, prompt, sid, project):
    from api.agent_harness import kernel

    monkeypatch.setattr(
        kernel, "get_agent", lambda _id, project_path=None: (manifest, adapter)
    )
    monkeypatch.setattr(
        "api.chat_run_registry.begin_run", lambda _s, _q: threading.Event()
    )
    return kernel.run_agent_web_command(
        manifest.id, prompt, sid, project_path=str(project)
    )


# --- delivery bookkeeping through the kernel seam ---


def test_resume_notice_sent_exactly_once(fake_stores, tmp_path, monkeypatch):
    """Full once, delta once after a rule change, bare after delivery is acked."""
    guest = _guest(tmp_path)
    manifest, adapter = _manifest(), _FakeAdapter()
    sid = "S-once"

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]

    (guest / ".cuttle" / "rules" / "01-a.md").write_text("Rule A v2", encoding="utf-8")
    adapter._resume = "cli-1"
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert DELTA_PREAMBLE in adapter.prompts[-1]
    assert "01-a.md" in adapter.prompts[-1]

    _run(monkeypatch, manifest, adapter, "hello three", sid, guest)
    assert adapter.prompts[-1] == "hello three"


def test_compile_alone_does_not_acknowledge(fake_stores, tmp_path):
    """Preparation is not delivery: compiling twice must repeat the delta."""
    from api.agent_harness.kernel import _compile_agent_prompt
    from types import SimpleNamespace

    guest = _guest(tmp_path)
    path = str(guest)
    cd.record_injected_snapshot("S-noack", "cursor", path)
    (guest / ".cuttle" / "rules" / "01-a.md").write_text("Rule A v2", encoding="utf-8")
    manifest = SimpleNamespace(id="cursor", env_profile="")

    before = cd.load_injected_snapshot("S-noack", "cursor", path)
    first, _, receipt1 = _compile_agent_prompt(
        manifest, "turn two", cwd=path, chat_session_id="S-noack", has_resume=True
    )
    second, _, receipt2 = _compile_agent_prompt(
        manifest, "turn three", cwd=path, chat_session_id="S-noack", has_resume=True
    )
    assert DELTA_PREAMBLE in first and DELTA_PREAMBLE in second
    assert receipt1 is not None and receipt2 is not None
    # Nothing was delivered, so the acknowledged snapshot is untouched.
    assert cd.load_injected_snapshot("S-noack", "cursor", path) == before


def test_failed_fresh_turn_gets_full_briefing_next(fake_stores, tmp_path, monkeypatch):
    """A failed full turn acks nothing: the resumed session gets the briefing."""
    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-failfresh"
    adapter = _FakeAdapter(succeed=False)

    out = _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    assert out.get("type") == "cursor_error"
    assert cd.load_injected_snapshot(sid, "cursor", str(guest)) is None

    adapter._succeed, adapter._resume = True, "cli-1"
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]


def test_failed_resume_turn_keeps_delta_pending(fake_stores, tmp_path, monkeypatch):
    """A failed delta turn retries the notice instead of losing it."""
    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-failresume"
    adapter = _FakeAdapter()

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    (guest / ".cuttle" / "rules" / "01-a.md").write_text("Rule A v2", encoding="utf-8")
    adapter._resume = "cli-1"

    adapter._succeed = False
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    adapter._succeed = True
    _run(monkeypatch, manifest, adapter, "hello three", sid, guest)
    assert DELTA_PREAMBLE in adapter.prompts[-1]
    assert "01-a.md" in adapter.prompts[-1]


def test_cancelled_turn_acknowledges_nothing(fake_stores, tmp_path, monkeypatch):
    """Cancel before execute: no delivery, no ack, notice stays pending."""
    from api.agent_harness import kernel

    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-cancel"
    adapter = _FakeAdapter()

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    (guest / ".cuttle" / "rules" / "01-a.md").write_text("Rule A v2", encoding="utf-8")
    adapter._resume = "cli-1"

    monkeypatch.setattr(kernel, "_run_was_cancelled", lambda _s: True)
    out = _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert "CANCELLED" in out.get("response", "")
    assert adapter.executions == 1  # only the fresh turn executed

    monkeypatch.setattr(kernel, "_run_was_cancelled", lambda _s: False)
    _run(monkeypatch, manifest, adapter, "hello three", sid, guest)
    assert DELTA_PREAMBLE in adapter.prompts[-1]


def test_meta_handled_turn_does_not_acknowledge(fake_stores, tmp_path, monkeypatch):
    """A meta answer is not adapter delivery: pending notices survive it."""
    from api.agent_harness.types import AgentResult

    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-meta"
    adapter = _FakeAdapter()

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    (guest / ".cuttle" / "rules" / "01-a.md").write_text("Rule A v2", encoding="utf-8")
    adapter._resume, adapter._meta = "cli-1", AgentResult(
        success=True, output="meta-ok"
    )
    out = _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert out.get("response") == "meta-ok"
    assert adapter.executions == 1

    adapter._meta = None
    _run(monkeypatch, manifest, adapter, "hello three", sid, guest)
    assert DELTA_PREAMBLE in adapter.prompts[-1]


def test_missing_snapshot_resume_gets_full_briefing(fake_stores, tmp_path, monkeypatch):
    """Native resume with no acknowledged snapshot: full briefing, never bare."""
    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-missing"
    adapter = _FakeAdapter(resume_id="cli-old")

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]
    assert "hello one" in adapter.prompts[-1]

    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert adapter.prompts[-1] == "hello two"


def test_mid_turn_edit_stays_pending(fake_stores, tmp_path, monkeypatch):
    """Ack the captured pre-execution snapshot, never a recomputed one."""
    guest = _guest(tmp_path)
    rule = guest / ".cuttle" / "rules" / "01-a.md"
    manifest = _manifest()
    sid = "S-midturn"
    adapter = _FakeAdapter(
        on_execute=lambda: rule.write_text("Rule A mid-turn edit", encoding="utf-8")
    )

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    adapter._resume = "cli-1"
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    # The ack captured the pre-execution state, so the mid-turn edit is new.
    assert DELTA_PREAMBLE in adapter.prompts[-1]
    assert "01-a.md" in adapter.prompts[-1]

    _run(monkeypatch, manifest, adapter, "hello three", sid, guest)
    assert adapter.prompts[-1] == "hello three"


def test_truncated_delta_falls_back_to_full(fake_stores, tmp_path, monkeypatch):
    """A delta that withholds instructions is never acked: send the briefing."""
    guest = _guest(tmp_path)
    rule = guest / ".cuttle" / "rules" / "01-a.md"
    manifest = _manifest()
    sid = "S-trunc"
    adapter = _FakeAdapter()

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    rule.write_text("BIG " + "x" * 5000, encoding="utf-8")
    adapter._resume = "cli-1"
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]
    assert DELTA_PREAMBLE not in adapter.prompts[-1]
    assert "… (truncated)" not in adapter.prompts[-1]

    _run(monkeypatch, manifest, adapter, "hello three", sid, guest)
    assert adapter.prompts[-1] == "hello three"


# --- preparation races and preparation failures ---


def test_full_prepare_detects_preparation_edit(fake_stores, tmp_path, monkeypatch):
    """A file changing mid-preparation voids the receipt, not the send."""
    from types import SimpleNamespace

    import api.cuttle_brain.context_compiler as compiler
    from api.agent_harness.kernel import _compile_agent_prompt

    guest = _guest(tmp_path)
    rule = guest / ".cuttle" / "rules" / "01-a.md"
    path = str(guest)
    manifest = SimpleNamespace(
        id="cursor", env_profile="", capabilities_inject="never"
    )
    real_compile = compiler.compile_context
    mutated = {"done": False}

    def _mutating_compile(*args, **kwargs):
        out = real_compile(*args, **kwargs)
        if not mutated["done"]:
            mutated["done"] = True
            rule.write_text("Rule A changed mid-preparation", encoding="utf-8")
        return out

    monkeypatch.setattr(compiler, "compile_context", _mutating_compile)
    prompt, meta, receipt = _compile_agent_prompt(
        manifest, "hello one", cwd=path, chat_session_id="S-racer", has_resume=False
    )
    assert FULL_PREAMBLE in prompt
    assert meta.get("mode") == "full"
    assert receipt is None
    assert cd.load_injected_snapshot("S-racer", "cursor", path) is None

    prompt2, _, receipt2 = _compile_agent_prompt(
        manifest, "hello two", cwd=path, chat_session_id="S-racer", has_resume=False
    )
    assert FULL_PREAMBLE in prompt2
    assert receipt2 is not None


def test_prepare_resume_detects_preparation_edit(fake_stores, tmp_path, monkeypatch):
    """A delta whose preparation raced an edit raises, never acks."""
    guest = _guest(tmp_path)
    rule = guest / ".cuttle" / "rules" / "01-a.md"
    path = str(guest)
    cd.record_injected_snapshot("S-rdelta", "cursor", path)
    before = cd.load_injected_snapshot("S-rdelta", "cursor", path)
    rule.write_text("Rule A v2", encoding="utf-8")

    real_compute = cd.compute_snapshot
    calls = {"n": 0}

    def _racing_compute(project_path):
        calls["n"] += 1
        if calls["n"] == 2:
            rule.write_text("Rule A v3 raced", encoding="utf-8")
        return real_compute(project_path)

    monkeypatch.setattr(cd, "compute_snapshot", _racing_compute)
    with pytest.raises(cd.UnstablePreparationError):
        cd.prepare_resume_delta("S-rdelta", "cursor", path)
    assert cd.load_injected_snapshot("S-rdelta", "cursor", path) == before


def test_unstable_delta_prepare_sends_full(fake_stores, tmp_path, monkeypatch):
    """A rule mutating mid-prepare sends full context, never a bare resume."""
    guest = _guest(tmp_path)
    rule = guest / ".cuttle" / "rules" / "01-a.md"
    manifest = _manifest()
    sid = "S-unstabledelta"
    adapter = _FakeAdapter()

    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    rule.write_text("Rule A v2", encoding="utf-8")

    real_compute = cd.compute_snapshot
    calls = {"n": 0}

    def _racing_compute(project_path):
        calls["n"] += 1
        if calls["n"] == 2:
            rule.write_text("Rule A v3 raced mid-prepare", encoding="utf-8")
        return real_compute(project_path)

    monkeypatch.setattr(cd, "compute_snapshot", _racing_compute)
    adapter._resume = "cli-1"
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]
    assert DELTA_PREAMBLE not in adapter.prompts[-1]
    assert "hello two" in adapter.prompts[-1]
    # Conservative ack: the full fallback prepared its own stable snapshot
    # of the raced-in state, so the next identical resume is bare.
    assert cd.load_injected_snapshot(sid, "cursor", str(guest)) is not None
    _run(monkeypatch, manifest, adapter, "hello three", sid, guest)
    assert adapter.prompts[-1] == "hello three"


def test_unstable_full_prepare_leaves_retry_pending(
    fake_stores, tmp_path, monkeypatch
):
    """Unstable preparation + successful turn still acks nothing."""
    import api.cuttle_brain.context_compiler as compiler

    guest = _guest(tmp_path)
    rule = guest / ".cuttle" / "rules" / "01-a.md"
    manifest = _manifest()
    sid = "S-unstable"
    adapter = _FakeAdapter()
    real_compile = compiler.compile_context
    mutated = {"done": False}

    def _mutating_compile(*args, **kwargs):
        out = real_compile(*args, **kwargs)
        if not mutated["done"]:
            mutated["done"] = True
            rule.write_text("Rule A changed mid-preparation", encoding="utf-8")
        return out

    monkeypatch.setattr(compiler, "compile_context", _mutating_compile)
    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]
    assert cd.load_injected_snapshot(sid, "cursor", str(guest)) is None

    adapter._resume = "cli-1"
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]


def test_unreadable_snapshot_falls_back_to_full(fake_stores, tmp_path, monkeypatch):
    """A snapshot load failure is missing context: full briefing, no receipt."""
    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-unreadable"
    adapter = _FakeAdapter(resume_id="cli-old")

    def _raise(*args, **kwargs):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(cd, "load_injected_snapshot", _raise)
    _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]
    assert DELTA_PREAMBLE not in adapter.prompts[-1]


def test_delta_prepare_failure_falls_back_to_full(fake_stores, tmp_path, monkeypatch):
    """A delta preparation failure sends the briefing, never a bare resume."""
    guest = _guest(tmp_path)
    path = str(guest)
    cd.record_injected_snapshot("S-prepfail", "cursor", path)
    (guest / ".cuttle" / "rules" / "01-a.md").write_text("Rule A v2", encoding="utf-8")
    manifest = _manifest()
    adapter = _FakeAdapter(resume_id="cli-1")

    def _raise(*args, **kwargs):
        raise RuntimeError("delta preparation failed")

    monkeypatch.setattr(cd, "prepare_resume_delta", _raise)
    _run(monkeypatch, manifest, adapter, "hello one", "S-prepfail", guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]
    assert DELTA_PREAMBLE not in adapter.prompts[-1]


def test_success_after_cancel_does_not_acknowledge(
    fake_stores, tmp_path, monkeypatch
):
    """A successful result that raced a cancellation acknowledges nothing."""
    from api.agent_harness import kernel

    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-cancelrace"
    cancelled = {"now": False}
    adapter = _FakeAdapter(on_execute=lambda: cancelled.update(now=True))

    monkeypatch.setattr(kernel, "_run_was_cancelled", lambda _s: cancelled["now"])
    out = _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    assert out.get("success") is True
    assert adapter.executions == 1
    assert cd.load_injected_snapshot(sid, "cursor", str(guest)) is None

    cancelled["now"] = False
    adapter._resume = "cli-1"
    _run(monkeypatch, manifest, adapter, "hello two", sid, guest)
    assert FULL_PREAMBLE in adapter.prompts[-1]


# --- receipt isolation and ranked visibility ---


def test_snapshot_failure_still_sends_full_briefing(fake_stores, tmp_path, monkeypatch):
    """Snapshot I/O failure must not degrade a compilable full briefing."""
    guest = _guest(tmp_path)
    manifest = _manifest()
    sid = "S-snapfail"
    adapter = _FakeAdapter()

    def _raise(project_path):
        raise OSError("snapshot store unreadable")

    monkeypatch.setattr(cd, "compute_snapshot", _raise)
    out = _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    assert out.get("success") is True
    assert FULL_PREAMBLE in adapter.prompts[-1]
    assert "Rule A" in adapter.prompts[-1]
    assert "hello one" in adapter.prompts[-1]
    assert cd.load_injected_snapshot(sid, "cursor", str(guest)) is None


def test_ranked_selection_visible_in_query_log_seam(
    fake_stores, tmp_path, monkeypatch
):
    """The existing set_brain seam carries bounded ranked selection, nothing private."""
    import api.query_tracker as tracker_mod
    from api.jev.client import FakeJevClient, set_client_override

    guest = _guest(tmp_path)
    big = "# Big beta\n" + "procedure line\n" * 800
    (guest / ".cuttle" / "docs").mkdir(parents=True)
    (guest / ".cuttle" / "docs" / "b.md").write_text(big, encoding="utf-8")
    manifest = _manifest()
    sid = "S-rankedseam"
    adapter = _FakeAdapter()

    override = FakeJevClient(
        handler=lambda state, q: {
            "needs_extra": {"type": "noul", "noul": 0.9},
            "pick": {
                "type": "choice",
                "choice": "doc:b.md",
                "confidence": 0.8,
                "probabilities": {"doc:b.md": 0.9, "doc:a.md": 0.1},
            },
        }
    )
    set_client_override(override)

    brains = {}

    class _FakeTracker:
        def __init__(self, qid):
            self.query_id = qid
            self.execution_data = None

        def set_harness(self, payload):
            pass

        def set_brain(self, payload):
            brains["brain"] = payload

        def add_event(self, *args, **kwargs):
            pass

        def set_sent(self, *args, **kwargs):
            pass

        def add_tool_call(self, *args, **kwargs):
            pass

    monkeypatch.setattr(tracker_mod, "get_query_tracker", lambda qid: _FakeTracker(qid))
    try:
        _run(monkeypatch, manifest, adapter, "hello one", sid, guest)
    finally:
        set_client_override(None)

    ranked = brains["brain"]["ranked"]
    assert ranked == {
        "choice": "doc:b.md",
        "confidence": 0.8,
        "injected": ["doc:b.md"],
        "excerpt": True,
        "skipped": False,
        "error": None,
    }
    assert "ranked_context" not in brains["brain"]
    assert "receipt" not in brains["brain"]
    assert "body" not in str(ranked)


# --- ranked query-metadata bounds ---


def test_ranked_query_metadata_strings_bounded(fake_stores, tmp_path, monkeypatch):
    """Long rank IDs are truncated in brain_meta; bodies never enter it."""
    from types import SimpleNamespace

    import api.cuttle_brain.context_compiler as compiler
    from api.agent_harness.kernel import _compile_agent_prompt
    from api.cuttle_brain.context_compiler import CompiledContext

    long_id = "doc:" + "x" * 200

    def _fake_compile(*args, **kwargs):
        return CompiledContext(
            schema_version=2,
            envelope="<cuttle_context>full</cuttle_context>",
            user_prompt="hi",
            layers_used=["core_contract"],
            meta={
                "ranked_context": {
                    "choice": long_id,
                    "confidence": 0.9,
                    "injected": [long_id, "doc:ok"],
                    "excerpt": False,
                    "skipped": False,
                }
            },
        )

    monkeypatch.setattr(compiler, "compile_context", _fake_compile)
    guest = _guest(tmp_path)
    manifest = SimpleNamespace(
        id="cursor", env_profile="", capabilities_inject="never"
    )
    _, meta, _ = _compile_agent_prompt(
        manifest, "hi", cwd=str(guest), chat_session_id="S-rankbound", has_resume=False
    )
    ranked = meta["ranked"]
    assert ranked["choice"] == long_id[:120]
    assert ranked["injected"] == [long_id[:120], "doc:ok"]
    assert all(len(v) <= 120 for v in ranked["injected"])
    assert "body" not in str(ranked)


# --- snapshot keys ---


def test_snapshot_keys_isolate_agents_projects_sessions(fake_stores, tmp_path):
    guest_a = _guest(tmp_path, "a")
    guest_b = _guest(tmp_path, "b")
    path_a, path_b = str(guest_a), str(guest_b)
    cd.record_injected_snapshot("K", "cursor", path_a)
    (guest_a / ".cuttle" / "rules" / "01-a.md").write_text("changed", encoding="utf-8")

    assert cd.build_resume_delta("K", "cursor", path_a) is not None
    assert cd.build_resume_delta("K", "codex", path_a) is None
    assert cd.build_resume_delta("K", "cursor", path_b) is None
    assert cd.build_resume_delta("K2", "cursor", path_a) is None


# --- GLOBAL.ini policy in snapshots/deltas ---


def test_resume_delta_respects_rules_off(fake_stores, fake_globals, tmp_path):
    """rules=off: a change to a suppressed global rule stays silent."""
    guest = _guest(tmp_path, router_ini="[global]\nrules = off\n")
    path = str(guest)
    cd.record_injected_snapshot("S-off", "cursor", path)
    fake_globals["rules"] = [("00-safety.md", SAFETY), ("00-core.md", "GLOBAL CORE v2")]
    assert cd.build_resume_delta("S-off", "cursor", path) is None


def test_safety_change_announced_under_rules_off(fake_stores, fake_globals, tmp_path):
    """rules=off retains the safety core in snapshots and deltas."""
    guest = _guest(tmp_path, router_ini="[global]\nrules = off\n")
    path = str(guest)
    cd.record_injected_snapshot("S-safety", "cursor", path)
    fake_globals["rules"] = [("00-safety.md", SAFETY + " amended"), ("00-core.md", "GLOBAL CORE v1")]
    delta = cd.build_resume_delta("S-safety", "cursor", path)
    assert delta is not None and "00-safety.md" in delta
    assert "00-core.md" not in delta


def test_resume_delta_respects_shadow(fake_stores, fake_globals, tmp_path):
    """shadow: a change to a retracted global twin stays silent."""
    guest = _guest(tmp_path, router_ini="[global]\nrules = shadow\n")
    (guest / ".cuttle" / "rules" / "00-core.md").write_text("GUEST CORE", encoding="utf-8")
    path = str(guest)
    cd.record_injected_snapshot("S-shadow", "cursor", path)
    fake_globals["rules"] = [("00-safety.md", SAFETY), ("00-core.md", "GLOBAL CORE v2")]
    assert cd.build_resume_delta("S-shadow", "cursor", path) is None


def test_policy_flip_announces_newly_visible_rules(fake_stores, fake_globals, tmp_path):
    """off→append flip: newly visible global rules are announced as new."""
    guest = _guest(tmp_path, router_ini="[global]\nrules = off\n")
    path = str(guest)
    cd.record_injected_snapshot("S-flip", "cursor", path)
    (guest / ".cuttle" / "GLOBAL.ini").write_text("[global]\nrules = append\n", encoding="utf-8")
    delta = cd.build_resume_delta("S-flip", "cursor", path)
    assert delta is not None and "00-core.md" in delta


def test_resume_delta_respects_docs_off(fake_stores, fake_globals, tmp_path, monkeypatch):
    """docs=off: a new global doc is not announced on resume."""
    global_root = tmp_path / "cuttle_global"
    (global_root / "docs").mkdir(parents=True)
    (global_root / "docs" / "a.md").write_text("# A", encoding="utf-8")
    monkeypatch.setattr(cd, "_cuttle_global_config", lambda: global_root)

    guest = _guest(tmp_path, router_ini="[global]\ndocs = off\n")
    path = str(guest)
    cd.record_injected_snapshot("S-docsoff", "cursor", path)
    (global_root / "docs" / "b.md").write_text("# B new", encoding="utf-8")
    assert cd.build_resume_delta("S-docsoff", "cursor", path) is None


def test_global_personal_only_doc_announced(fake_stores, fake_globals, tmp_path, monkeypatch):
    """Merged global inventory: a personal-only global doc is announced."""
    global_root = tmp_path / "cuttle_global"
    (global_root / "docs").mkdir(parents=True)
    (global_root / "docs" / "a.md").write_text("# A", encoding="utf-8")
    monkeypatch.setattr(cd, "_cuttle_global_config", lambda: global_root)

    guest = _guest(tmp_path)
    path = str(guest)
    cd.record_injected_snapshot("S-gpers", "cursor", path)
    personal_docs = global_root / "personal" / "docs"
    personal_docs.mkdir(parents=True)
    (personal_docs / "local.md").write_text("# local only", encoding="utf-8")
    delta = cd.build_resume_delta("S-gpers", "cursor", path)
    assert delta is not None and "local.md" in delta


def test_prepare_marks_truncated(fake_stores, tmp_path):
    """prepare_resume_delta flags deltas that withhold instructions."""
    guest = _guest(tmp_path)
    path = str(guest)
    cd.record_injected_snapshot("S-truncflag", "cursor", path)
    (guest / ".cuttle" / "rules" / "01-a.md").write_text(
        "BIG " + "x" * 5000, encoding="utf-8"
    )
    plan = cd.prepare_resume_delta("S-truncflag", "cursor", path)
    assert plan is not None and plan.truncated is True
    assert cd.is_truncated_delta_text(plan.text) is True


# --- personal overlay guards ---


def test_personal_twin_change_triggers_delta(fake_stores, fake_globals, tmp_path):
    """An install-local personal twin edit is announced."""
    guest = _guest(tmp_path)
    personal = guest / ".cuttle" / "personal" / "rules"
    personal.mkdir(parents=True)
    (personal / "01-a.md").write_text("local line 1", encoding="utf-8")
    path = str(guest)
    cd.record_injected_snapshot("S-pers", "cursor", path)
    (personal / "01-a.md").write_text("local line 1 + line 2", encoding="utf-8")
    delta = cd.build_resume_delta("S-pers", "cursor", path)
    assert delta is not None and "01-a.md" in delta


def test_personal_only_rule_triggers_delta(fake_stores, fake_globals, tmp_path):
    """A personal-only rule file is announced as new."""
    guest = _guest(tmp_path)
    path = str(guest)
    cd.record_injected_snapshot("S-pers-only", "cursor", path)
    personal = guest / ".cuttle" / "personal" / "rules"
    personal.mkdir(parents=True)
    (personal / "09-local.md").write_text("local only", encoding="utf-8")
    delta = cd.build_resume_delta("S-pers-only", "cursor", path)
    assert delta is not None and "09-local.md" in delta
