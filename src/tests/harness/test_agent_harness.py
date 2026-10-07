"""Contract tests for Agent Harness (folder-per-agent pilots)."""

from __future__ import annotations

import api.agent_harness.catalog as catalog
from api.agent_harness.catalog import (
    get_agent,
    list_agents,
    match_slash_command,
    public_catalog,
    reload_catalog,
    sticky_prefixes_from_harness,
)


def setup_function(_fn=None):
    reload_catalog()


def test_discovers_bundled_harness_agents():
    ids = list_agents()
    for expected in (
        "opencode",
        "antigravity",
        "cursor",
        "codex",
        "muse",
        "claude",
        "hermes",
        "deepseek",
    ):
        assert expected in ids, f"missing bundled agent {expected}"


def test_manifests_have_slash_and_sticky():
    for aid in (
        "opencode",
        "antigravity",
        "cursor",
        "codex",
        "muse",
        "claude",
        "hermes",
        "deepseek",
    ):
        pair = get_agent(aid)
        assert pair is not None
        manifest, adapter = pair
        assert manifest.slash_prefix().startswith("/")
        assert manifest.slash_prefix().endswith(" ")
        assert manifest.sticky is True
        assert callable(adapter.available)
        assert callable(adapter.execute)


def test_match_slash_command():
    assert match_slash_command('/opencode "hi there"') == ("opencode", "hi there")
    assert match_slash_command("/antigravity review this") == (
        "antigravity",
        "review this",
    )
    assert match_slash_command("/cursor do stuff") == ("cursor", "do stuff")
    assert match_slash_command("/codex fix this") == ("codex", "fix this")
    assert match_slash_command("/muse hello") == ("muse", "hello")
    assert match_slash_command('/claude "hi"') == ("claude", "hi")
    assert match_slash_command("/hermes local") == ("hermes", "local")
    assert match_slash_command("/deepseek ping") == ("deepseek", "ping")


def test_match_harness_slash_uses_catalog_commands():
    from api.web_chat_api import _match_harness_slash

    assert _match_harness_slash("/cursor list files") == ("cursor", "list files")
    assert _match_harness_slash("/codex") == ("codex", "")
    assert _match_harness_slash("plain prompt") is None


def test_public_catalog_shape():
    rows = public_catalog()
    by_id = {r["id"]: r for r in rows}
    assert "opencode" in by_id
    assert "antigravity" in by_id
    assert "cursor" in by_id
    assert "codex" in by_id
    assert "muse" in by_id
    assert "claude" in by_id
    assert "hermes" in by_id
    assert by_id["hermes"]["harness"] is True
    assert by_id["hermes"]["requires_cloud"] is False
    assert "deepseek" in by_id
    assert by_id["deepseek"]["harness"] is True
    assert by_id["deepseek"]["requires_cloud"] is True
    # BYO-CLI (P6-A): nothing is installable or auto-installed; guidance stays.
    assert by_id["deepseek"]["installable"] is False
    assert by_id["antigravity"]["installable"] is False
    assert by_id["antigravity"]["auto_install"] is False
    assert by_id["deepseek"]["install_hint"] or by_id["deepseek"]["hint"]
    assert by_id["antigravity"]["install_hint"] or by_id["antigravity"]["hint"]
    assert by_id["cursor"]["harness"] is True
    # Gemini is installed on this machine in dogfood; opencode may not be.
    assert isinstance(by_id["opencode"]["available"], bool)


def test_sticky_prefixes_include_pilots():
    prefixes = sticky_prefixes_from_harness()
    for p in (
        "/opencode ",
        "/antigravity ",
        "/cursor ",
        "/codex ",
        "/muse ",
        "/claude ",
        "/hermes ",
        "/deepseek ",
    ):
        assert p in prefixes


def test_starred_slash_allows_opencode():
    from api.starred_slash import normalize_sticky_prefix, refresh_sticky_prefixes

    refresh_sticky_prefixes()
    assert normalize_sticky_prefix("/opencode") == "/opencode "
    assert normalize_sticky_prefix("/cursor") == "/cursor "


def test_dispatch_registers_harness_runners():
    from api.agent_router.dispatch import _default_runners

    runners = _default_runners()
    for aid in (
        "opencode",
        "antigravity",
        "cursor",
        "codex",
        "muse",
        "claude",
        "hermes",
        "deepseek",
    ):
        assert aid in runners


def test_cursor_adapter_available_is_bool():
    from api.agent_harness.agents.cursor.adapter import build_adapter

    ad = build_adapter()
    assert isinstance(ad.available(), bool)


def test_codex_adapter_missing_cli(monkeypatch):
    from api.agent_harness.agents.codex import adapter as cx

    monkeypatch.setattr(
        "scripts.utilities.codex_cli_tool.codex_executable", lambda: None
    )
    ad = cx.build_adapter()
    assert ad.available() is False


def test_claude_manifest_enables_resume():
    pair = get_agent("claude")
    assert pair is not None
    manifest, adapter = pair
    assert manifest.resume is True
    assert manifest.slash_prefix() == "/claude "
    # No pin yet — load returns None; kernel still treats resume as supported.
    assert adapter.load_resume(".", "123") is None
    assert isinstance(adapter.available(), bool)


def test_hermes_is_local_first_and_supports_resume():
    pair = get_agent("hermes")
    assert pair is not None
    manifest, adapter = pair
    assert manifest.requires_cloud is False
    assert manifest.resume is True
    assert manifest.slash_prefix() == "/hermes "
    # No pin yet — load returns None; kernel still treats resume as supported.
    assert adapter.load_resume(".", "123") is None
    assert isinstance(adapter.available(), bool)


def test_hermes_adapter_missing_cli(monkeypatch):
    from api.agent_harness.agents.hermes import adapter as hm

    monkeypatch.setattr(
        "scripts.utilities.hermes_cli_tool.hermes_executable", lambda: None
    )
    ad = hm.build_adapter()
    assert ad.available() is False


def test_deepseek_is_cloud_and_skips_resume():
    from api.agent_router.registry import normalize_agent_id

    pair = get_agent("deepseek")
    assert pair is not None
    manifest, adapter = pair
    assert manifest.requires_cloud is True
    assert manifest.resume is False
    assert manifest.slash_prefix() == "/deepseek "
    assert manifest.smoke_model == "deepseek-v4-flash"
    assert adapter.load_resume(".", "123") is None
    assert isinstance(adapter.available(), bool)
    assert normalize_agent_id("dsh") == "deepseek"
    assert get_agent("dsh") is not None


def test_deepseek_adapter_missing_cli(monkeypatch):
    from api.agent_harness.agents.deepseek import adapter as ds

    monkeypatch.setattr(ds, "dsh_argv", lambda: None)
    ad = ds.build_adapter()
    assert ad.available() is False


def test_muse_env_profile_is_native():
    pair = get_agent("muse")
    assert pair is not None
    assert pair[0].env_profile == "native"


# --------------------------------------------------------------------------
# BYO-CLI retirement (Phase 6 P6-A): the executable installer machinery
# (`api.agent_harness.installer`: npm installs, remote-script download +
# execute) is removed. Discovery, availability/version validation,
# invocation, capability normalization, and manifest install guidance
# remain. These pins guard the retirement — they fail if executable
# install machinery is reintroduced.
# --------------------------------------------------------------------------


def test_executable_installer_module_is_gone():
    import pytest

    with pytest.raises(ImportError):
        import api.agent_harness.installer  # noqa: F401


def test_kernel_has_no_installer_import():
    from pathlib import Path as _Path

    kernel_text = (
        _Path(__file__).resolve().parents[2] / "api" / "agent_harness" / "kernel.py"
    ).read_text(encoding="utf-8")
    assert "install_agent_cli" not in kernel_text
    assert "agent_harness.installer" not in kernel_text
    assert "auto_install" not in kernel_text


def test_missing_cli_guidance_does_not_shell_out(monkeypatch, tmp_path):
    import subprocess
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest

    class _Adapter:
        def available(self):
            return False

    manifest = AgentManifest(
        id="gone",
        label="Gone CLI",
        slash="/gone",
        models=[],
        resume=False,
        capabilities_inject="never",
        env_profile="native",
        activity="heartbeat",
        missing_cli_hint="Install Gone from https://example.invalid/gone.",
        notes="",
        hint="",
        install_hint="",
        install_kind="",
        install_package="",
        install_url_windows="",
        install_url_posix="",
        executable_names=["gone"],
        auto_install=True,  # must be ignored: no machinery reads it
        schema_version=1,
        source="test",
    )
    monkeypatch.setattr(
        kernel, "get_agent", lambda _id, project_path=None: (manifest, _Adapter())
    )

    def _no_shell(*args, **kwargs):
        raise AssertionError("BYO-CLI: installer machinery must not shell out")

    monkeypatch.setattr(subprocess, "run", _no_shell)
    out = kernel.run_agent_web_command(
        "gone", "do work", 4242, project_path=str(tmp_path)
    )
    assert out.get("success") is True
    assert "Install Gone from https://example.invalid/gone." in out.get("response", "")
    assert out.get("type") == "gone_error"


def test_normalize_chat_session_id():
    from api.agent_harness.types import normalize_chat_session_id

    assert normalize_chat_session_id(148) == "148"
    assert normalize_chat_session_id("db_session_148") == "db_session_148"
    assert normalize_chat_session_id(None) is None
    assert normalize_chat_session_id("  ") is None


def test_kernel_filters_unsupported_execute_kwargs(monkeypatch, tmp_path):
    """Kernel must not pass cancel_event to adapters that do not accept it."""
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult

    seen: dict = {}

    class _FakeAdapter:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return project_path or "."

        def load_resume(self, cwd, chat_session_id):
            return None

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            pass

        def clear_resume(self, cwd, chat_session_id):
            pass

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
            seen["prompt"] = prompt
            return AgentResult(success=True, output="pong", session_id="oc-1")

    manifest = AgentManifest(
        id="opencode",
        label="OpenCode",
        slash="/opencode",
        requires_cloud=True,
        sticky=True,
        default_model="openrouter/z-ai/glm-5.3-flash",
        models=["openrouter/z-ai/glm-5.3-flash"],
        resume=True,
        capabilities_inject="never",
        env_profile="native",
        activity="heartbeat",
        missing_cli_hint="",
        notes="",
        hint="",
        install_hint="",
        install_kind="",
        install_package="",
        install_url_windows="",
        install_url_posix="",
        executable_names=[],
        auto_install=False,
        schema_version=1,
        source="test",
    )

    monkeypatch.setattr(kernel, "get_agent", lambda _id, project_path=None: (manifest, _FakeAdapter()))
    monkeypatch.setattr(
        kernel,
        "_compile_agent_prompt",
        lambda *args, **kwargs: (
            kwargs.get("prompt") or (args[1] if len(args) > 1 else ""),
            {},
        ),
    )
    monkeypatch.setattr(
        "api.chat_run_registry.begin_run",
        lambda sid, qid: __import__("threading").Event(),
    )

    out = kernel.run_agent_web_command(
        "opencode",
        "Reply pong",
        9001,
        project_path=str(tmp_path),
    )
    assert out.get("success") is True
    assert seen.get("prompt") == "Reply pong"
    # Raises TypeError if kernel passes cancel_event without filtering.


def test_opencode_model_command_sets_and_lists(monkeypatch):
    from api.agent_harness.agents.opencode import model_catalog as oc_catalog
    from api.agent_harness.agents.opencode.adapter import (
        DEFAULT_OPENCODE_MODEL,
        Adapter,
    )
    from api.agent_harness.agents.opencode.session_store import (
        load_opencode_model,
        save_opencode_model,
    )

    # Deterministic fixture at the owner seam: the bare-list branch calls
    # the owner-exported list_opencode_catalog_models (deferred-imported by
    # the adapter at call time), which shells to `opencode models --verbose`
    # when no fresh cache exists — correctly blocked by the vendor guard.
    # Fake it so the list never depends on host install/network.
    def _fake_catalog(*, limit=None, **kwargs):
        models = [
            {"id": "openrouter/z-ai/glm-5.3-flash", "label": "GLM 5.3 Flash (OpenRouter)"},
            {"id": "openai/gpt-4o-mini", "label": "GPT-4o mini (OpenAI)"},
        ]
        rows = models[: int(limit)] if limit else models
        return {
            "models": rows,
            "source": "test-fixture",
            "error": None,
            "fetched_at": 0.0,
            "count": len(models),
            "returned": len(rows),
        }

    monkeypatch.setattr(oc_catalog, "list_opencode_catalog_models", _fake_catalog)

    adapter = Adapter()
    sid = "oc-model-session"
    save_opencode_model(sid, None)

    listed = adapter.handle_meta("model", chat_session_id=sid, model=DEFAULT_OPENCODE_MODEL)
    assert listed is not None and listed.success
    assert "openrouter/z-ai/glm-5.3-flash" in (listed.output or "")

    setter = adapter.handle_meta(
        "model openai/gpt-4o-mini",
        chat_session_id=sid,
        model=DEFAULT_OPENCODE_MODEL,
    )
    assert setter is not None and setter.success
    assert load_opencode_model(sid) == "openai/gpt-4o-mini"

    reset = adapter.handle_meta("model reset", chat_session_id=sid, model=DEFAULT_OPENCODE_MODEL)
    assert reset is not None and reset.success
    assert load_opencode_model(sid) is None


def test_opencode_model_command_does_not_hijack_real_prompts():
    from api.agent_harness.agents.opencode.adapter import Adapter, DEFAULT_OPENCODE_MODEL

    adapter = Adapter()
    assert (
        adapter.handle_meta(
            "model the login flow", chat_session_id="s", model=DEFAULT_OPENCODE_MODEL
        )
        is None
    )


def test_opencode_effort_command_sets_and_lists(tmp_path, monkeypatch):
    from api.agent_harness.agents.opencode import adapter as oc
    from api.agent_harness.agents.opencode import session_store as store

    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "opencode_map.json")
    adapter = oc.Adapter()
    sid = "oc-effort-session"

    result = adapter.handle_meta("/effort high", chat_session_id=sid, model=None)
    assert result is not None and result.success
    assert result.meta.get("agent_effort") == "high"
    assert store.load_opencode_effort(sid) == "high"

    listed = adapter.handle_meta("/effort", chat_session_id=sid, model=None)
    assert listed is not None and "high" in (listed.output or "")

    assert adapter.handle_meta("effort the login flow", chat_session_id=sid, model=None) is None

    unknown = adapter.handle_meta("/effort banana", chat_session_id=sid, model=None)
    assert unknown is not None and "Unknown effort" in (unknown.output or "")
    assert store.load_opencode_effort(sid) == "high"

    reset = adapter.handle_meta("/effort reset", chat_session_id=sid, model=None)
    assert reset is not None and reset.success
    assert store.load_opencode_effort(sid) is None


def test_opencode_effort_store_roundtrip(tmp_path, monkeypatch):
    from api.agent_harness.agents.opencode import session_store as store

    monkeypatch.setattr(store, "_map_file", lambda: tmp_path / "opencode_map.json")
    assert store.load_opencode_effort("s1") is None
    assert store.save_opencode_effort("s1", "max") == "max"
    assert store.load_opencode_effort("s1") == "max"
    assert store.save_opencode_effort("s1", None) is None
    assert store.load_opencode_effort("s1") is None


def test_public_catalog_icon_tiles_come_from_manifests():
    """Settings tiles render agent.icon from the manifest, never a page map."""
    from api.agent_harness.types import AgentManifest

    rows = public_catalog()
    by_id = {r["id"]: r for r in rows}
    for aid, pair in (("cursor", get_agent("cursor")), ("codex", get_agent("codex"))):
        assert pair is not None
        assert pair[0].icon, f"{aid} manifest needs an icon glyph"
        assert by_id[aid]["icon"] == pair[0].icon
    # Wire shape stays boring: short glyph, always present, never prose.
    for row in rows:
        assert "icon" in row
        assert isinstance(row["icon"], str)
        assert len(row["icon"]) <= 8
    # Default: empty manifest icon falls back to the label initial in the page.
    assert AgentManifest(id="x", label="X", slash="/x").to_public_dict()["icon"] == ""


def test_public_catalog_install_ux_fields():
    rows = public_catalog()
    by_id = {r["id"]: r for r in rows}
    row = by_id["opencode"]
    assert row["status"] in ("ready", "missing_cli")
    assert "install_hint" in row
    assert row["source"] == "bundled"
    assert row.get("schema_version", 1) >= 1
    assert isinstance(row["available"], bool)
    if not row["available"]:
        assert row["status"] == "missing_cli"
        assert row["hint"]  # install line surfaced as palette hint


def test_kernel_passes_str_session_id_to_resume(monkeypatch):
    """Kernel coerces DB int → str before load_resume / execute / save_resume."""
    import asyncio
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult

    seen = {"load": None, "save": None, "execute": None}

    class _FakeAdapter:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return project_path or "."

        def load_resume(self, cwd, chat_session_id):
            seen["load"] = chat_session_id
            return None

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            seen["save"] = chat_session_id

        def clear_resume(self, cwd, chat_session_id):
            pass

        async def execute(self, prompt, **kwargs):
            seen["execute"] = kwargs.get("chat_session_id")
            return AgentResult(success=True, output="pong", session_id="cli-sid-1")

    manifest = AgentManifest(
        id="fakeprobe",
        label="Fake",
        slash="/fakeprobe",
        resume=True,
        install_hint="n/a",
    )

    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, _FakeAdapter())
    )
    # Avoid query-report / run-registry side effects.
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

    out = kernel.run_agent_web_command("fakeprobe", "hi", 148, project_path=".")
    assert out.get("response") == "pong"
    assert seen["load"] == "148"
    assert seen["execute"] == "148"
    assert seen["save"] == "148"
    assert isinstance(seen["load"], str)


def test_kernel_attaches_usage_to_web_response(monkeypatch):
    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult

    class _FakeAdapter:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return project_path or "."

        def load_resume(self, cwd, chat_session_id):
            return None

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            pass

        def clear_resume(self, cwd, chat_session_id):
            pass

        async def execute(self, prompt, **kwargs):
            return AgentResult(
                success=True,
                output="done",
                usage={
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                    "cost": 0.0025,
                },
            )

    manifest = AgentManifest(
        id="usageprobe",
        label="Usage Probe",
        slash="/usageprobe",
        resume=True,
        install_hint="n/a",
    )
    tracker_calls = []

    class _FakeTracker:
        query_id = "qid"
        execution_data = {"tool_calls": []}

        def add_tool_call(self, *args, **kwargs):
            tracker_calls.append(kwargs)

    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, _FakeAdapter())
    )
    monkeypatch.setattr(
        "api.query_tracker.start_query_tracking", lambda *a, **k: "qid"
    )
    monkeypatch.setattr(
        "api.query_tracker.finish_query_tracking", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "api.query_tracker.get_query_tracker", lambda *a, **k: _FakeTracker()
    )
    monkeypatch.setattr("api.active_executions.register_execution", lambda *a, **k: None)
    monkeypatch.setattr("api.active_executions.unregister_execution", lambda *a, **k: None)

    out = kernel.run_agent_web_command("usageprobe", "hi", 1, project_path=".")
    assert out["usage"] == {
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "cost": 0.0025,
    }
    assert out["cost"] == 0.0025
    assert tracker_calls
    assert tracker_calls[0]["tokens"]["total_tokens"] == 150
    assert tracker_calls[0]["cost"] == 0.0025


def test_dropin_discovery_from_user_root(tmp_path, monkeypatch):
    """User harness_agents root can add a new agent without touching bundled tree."""
    agent_dir = tmp_path / "echoagent"
    agent_dir.mkdir()
    (agent_dir / "manifest.yaml").write_text(
        "id: echoagent\nlabel: Echo\nslash: /echoagent\nrequires_cloud: false\n"
        "sticky: true\nresume: false\ninstall_hint: n/a\n",
        encoding="utf-8",
    )
    (agent_dir / "adapter.py").write_text(
        "from api.agent_harness.types import AgentResult\n"
        "class Adapter:\n"
        "    def available(self):\n"
        "        return True\n"
        "    def resolve_cwd(self, project_path):\n"
        "        return project_path or '.'\n"
        "    def load_resume(self, cwd, chat_session_id):\n"
        "        return None\n"
        "    def save_resume(self, cwd, chat_session_id, cli_session_id):\n"
        "        return None\n"
        "    def clear_resume(self, cwd, chat_session_id):\n"
        "        return None\n"
        "    async def execute(self, prompt, **kwargs):\n"
        "        return AgentResult(success=True, output=prompt or '')\n"
        "def build_adapter():\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CUTTLE_AGENTS_DIR", str(tmp_path))
    reload_catalog()
    try:
        assert "echoagent" in list_agents()
        assert match_slash_command("/echoagent ping") == ("echoagent", "ping")
        pair = get_agent("echoagent")
        assert pair is not None
        assert pair[0].source == "user"
        rows = {r["id"]: r for r in public_catalog()}
        assert rows["echoagent"]["source"] == "user"
        assert rows["echoagent"]["available"] is True
    finally:
        monkeypatch.delenv("CUTTLE_AGENTS_DIR", raising=False)
        reload_catalog()


def test_dropin_cannot_shadow_bundled(tmp_path, monkeypatch):
    """A drop-in folder named opencode must not replace the bundled connector."""
    agent_dir = tmp_path / "opencode"
    agent_dir.mkdir()
    (agent_dir / "manifest.yaml").write_text(
        "id: opencode\nlabel: Fake OpenCode\nslash: /opencode\ninstall_hint: fake\n",
        encoding="utf-8",
    )
    (agent_dir / "adapter.py").write_text(
        "class Adapter:\n"
        "    def available(self):\n"
        "        return False\n"
        "    def resolve_cwd(self, p):\n"
        "        return p or '.'\n"
        "    def load_resume(self, *a):\n"
        "        return None\n"
        "    def save_resume(self, *a):\n"
        "        return None\n"
        "    def clear_resume(self, *a):\n"
        "        return None\n"
        "    async def execute(self, prompt, **kwargs):\n"
        "        from api.agent_harness.types import AgentResult\n"
        "        return AgentResult(success=False, error='shadow')\n"
        "def build_adapter():\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CUTTLE_AGENTS_DIR", str(tmp_path))
    reload_catalog()
    try:
        pair = get_agent("opencode")
        assert pair is not None
        assert pair[0].source == "bundled"
        assert pair[0].label != "Fake OpenCode"
    finally:
        monkeypatch.delenv("CUTTLE_AGENTS_DIR", raising=False)
        reload_catalog()


def test_dropin_does_not_prepend_sys_path(tmp_path, monkeypatch):
    """Drop-in dirs must not sit at sys.path[0] (stdlib / Cuttle shadowing)."""
    import sys

    agent_dir = tmp_path / "pathagent"
    agent_dir.mkdir()
    (agent_dir / "json.py").write_text("shadow = True\n", encoding="utf-8")
    (agent_dir / "manifest.yaml").write_text(
        "id: pathagent\nlabel: Path\nslash: /pathagent\ninstall_hint: n/a\n",
        encoding="utf-8",
    )
    (agent_dir / "adapter.py").write_text(
        "from api.agent_harness.types import AgentResult\n"
        "class Adapter:\n"
        "    def available(self):\n"
        "        return True\n"
        "    def resolve_cwd(self, project_path):\n"
        "        return project_path or '.'\n"
        "    def load_resume(self, cwd, chat_session_id):\n"
        "        return None\n"
        "    def save_resume(self, cwd, chat_session_id, cli_session_id):\n"
        "        return None\n"
        "    def clear_resume(self, cwd, chat_session_id):\n"
        "        return None\n"
        "    async def execute(self, prompt, **kwargs):\n"
        "        return AgentResult(success=True, output=prompt or '')\n"
        "def build_adapter():\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CUTTLE_AGENTS_DIR", str(tmp_path))
    before0 = sys.path[0]
    reload_catalog()
    try:
        assert "pathagent" in list_agents()
        assert sys.path[0] == before0
        import json as json_mod

        assert not hasattr(json_mod, "shadow")
    finally:
        monkeypatch.delenv("CUTTLE_AGENTS_DIR", raising=False)
        reload_catalog()


def test_project_dropin_discovery(tmp_path, monkeypatch):
    agents = tmp_path / ".cuttle" / "agents" / "projbot"
    agents.mkdir(parents=True)
    (agents / "manifest.yaml").write_text(
        "id: projbot\nlabel: ProjBot\nslash: /projbot\nrequires_cloud: false\n"
        "sticky: true\nresume: false\ninstall_hint: n/a\n",
        encoding="utf-8",
    )
    (agents / "adapter.py").write_text(
        "from api.agent_harness.types import AgentResult\n"
        "class Adapter:\n"
        "    def available(self):\n"
        "        return True\n"
        "    def resolve_cwd(self, project_path):\n"
        "        return project_path or '.'\n"
        "    def load_resume(self, *a):\n"
        "        return None\n"
        "    def save_resume(self, *a):\n"
        "        return None\n"
        "    def clear_resume(self, *a):\n"
        "        return None\n"
        "    async def execute(self, prompt, **kwargs):\n"
        "        return AgentResult(success=True, output='ok')\n"
        "def build_adapter():\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CUTTLE_ALLOW_PROJECT_ADAPTERS", "1")
    assert "projbot" not in list_agents()
    assert "projbot" in list_agents(str(tmp_path))
    assert match_slash_command("/projbot hi", project_path=str(tmp_path)) == (
        "projbot",
        "hi",
    )
    pair = get_agent("projbot", project_path=str(tmp_path))
    assert pair is not None
    assert pair[0].source == "project"


def test_project_dropin_not_loaded_without_opt_in(tmp_path, monkeypatch):
    marker = tmp_path / "imported.txt"
    agents = tmp_path / ".cuttle" / "agents" / "evilbot"
    agents.mkdir(parents=True)
    (agents / "manifest.yaml").write_text(
        "id: evilbot\nlabel: Evil\nslash: /evilbot\nrequires_cloud: false\n"
        "sticky: false\nresume: false\ninstall_hint: n/a\n",
        encoding="utf-8",
    )
    (agents / "adapter.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
        "class Adapter:\n"
        "    def available(self):\n"
        "        return True\n"
        "    def resolve_cwd(self, project_path):\n"
        "        return project_path or '.'\n"
        "    def load_resume(self, *a):\n"
        "        return None\n"
        "    def save_resume(self, *a):\n"
        "        return None\n"
        "    def clear_resume(self, *a):\n"
        "        return None\n"
        "    async def execute(self, prompt, **kwargs):\n"
        "        from api.agent_harness.types import AgentResult\n"
        "        return AgentResult(success=True, output='ok')\n"
        "def build_adapter():\n"
        "    return Adapter()\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CUTTLE_ALLOW_PROJECT_ADAPTERS", raising=False)
    reload_catalog()
    assert "evilbot" not in list_agents(str(tmp_path))
    assert get_agent("evilbot", project_path=str(tmp_path)) is None
    assert not marker.exists()


def test_smoke_policy_min_scope_and_per_agent_model(monkeypatch):
    from api.agent_harness.smoke_policy import live_agent_ids, live_model, live_scopes
    from api.agent_harness.types import AgentManifest

    monkeypatch.setenv("CUTTLE_AGENT_SMOKE_AGENTS", "deepseek")
    monkeypatch.setenv("CUTTLE_AGENT_SMOKE_SCOPE", "min")
    monkeypatch.delenv("CUTTLE_AGENT_SMOKE_MODEL", raising=False)
    monkeypatch.delenv("CUTTLE_AGENT_SMOKE_MODEL_DEEPSEEK", raising=False)
    assert live_agent_ids(["cursor", "muse", "deepseek"]) == ["deepseek"]
    assert live_scopes() == frozenset({"one_shot"})
    manifest = AgentManifest(
        id="deepseek",
        label="DeepSeek",
        slash="/deepseek",
        default_model="deepseek-v4-pro",
        smoke_model="deepseek-v4-flash",
    )
    assert live_model("deepseek", manifest) == "deepseek-v4-flash"
    monkeypatch.setenv("CUTTLE_AGENT_SMOKE_MODEL_DEEPSEEK", "deepseek-v4-pro")
    assert live_model("deepseek", manifest) == "deepseek-v4-pro"


def test_dsh_argv_skips_cmd_shim(tmp_path, monkeypatch):
    from api.agent_harness.agents.deepseek import adapter as ds

    shim = tmp_path / "dsh.cmd"
    bin_js = tmp_path / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    bin_js.parent.mkdir(parents=True)
    shim.write_text("@echo off\n", encoding="utf-8")
    bin_js.write_text("console.log('dsh')\n", encoding="utf-8")

    def _which(names, env_var=""):
        names = tuple(names)
        if "dsh" in names:
            return str(shim)
        if "node" in names:
            return r"C:\nodejs\node.exe"
        return None

    monkeypatch.setattr(ds, "which_preferring_native", _which)
    argv = ds.dsh_argv()
    assert argv is not None
    assert argv[0].endswith("node.exe")
    assert argv[1] == str(bin_js)


def test_deepseek_long_prompt_uses_tempfile_not_argv(monkeypatch, tmp_path):
    import asyncio
    from api.agent_harness.agents.deepseek import adapter as ds

    prompt = ("context-padding-" * 1600) + "\nEND-SENTINEL-DO-NOT-DROP"
    captured = {}

    class _ByteReader:
        def __init__(self, data: bytes):
            self._data = data
            self._done = False

        async def readline(self):
            if self._done:
                return b""
            self._done = True
            return self._data if self._data.endswith(b"\n") else self._data + b"\n"

        async def read(self, _size=-1):
            if self._done:
                return b""
            self._done = True
            return self._data

    class _Process:
        returncode = 0

        def __init__(self):
            self.stdout = _ByteReader(b'{"type":"final","text":"pong"}\n')
            self.stderr = _ByteReader(b"")

        async def wait(self):
            return 0

        def kill(self):
            self.returncode = -9

    async def _spawn(*args, **kwargs):
        captured["argv"] = args
        assert "DEEPSEEK_API_KEY" not in kwargs["env"]
        return _Process()

    monkeypatch.setattr(ds, "dsh_argv", lambda: ["node.exe", "bin.js"])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "host-only-test-secret")
    monkeypatch.setattr(ds.asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(ds, "attach_to_chat_run", lambda *a, **k: None)

    result = asyncio.run(
        ds.Adapter().execute(
            prompt,
            cwd=str(tmp_path),
            resume=None,
            model="deepseek-v4-flash",
            timeout=5,
        )
    )
    assert result.success is True
    argv = captured["argv"]
    assert not any("END-SENTINEL-DO-NOT-DROP" in str(arg) for arg in argv)
    task = argv[-1]
    assert "Read the UTF-8 file" in task
    assert "--patch" not in argv  # default Flash needs no overlay


def test_resolve_harness_cwd_keeps_project_not_process_cwd(tmp_path, monkeypatch):
    from pathlib import Path

    from api.agent_harness.cwd import resolve_harness_cwd, same_project

    other = tmp_path / "flask-cwd"
    other.mkdir()
    project = tmp_path / "escape-purgatory"
    project.mkdir()
    monkeypatch.chdir(other)

    got = Path(resolve_harness_cwd(str(project)))
    assert got.resolve() == project.resolve()
    assert not same_project(str(other), str(got))


def test_resolve_harness_cwd_nested_git(tmp_path):
    import subprocess
    from pathlib import Path

    from api.agent_harness.cwd import resolve_harness_cwd

    root = tmp_path / "DemoGame"
    source = root / "source"
    source.mkdir(parents=True)
    subprocess.run(
        ["git", "init"],
        cwd=str(source),
        check=True,
        capture_output=True,
    )
    got = Path(resolve_harness_cwd(str(root))).resolve()
    assert got == source.resolve()


def test_every_bundled_adapter_honors_project_chip(tmp_path, monkeypatch):
    """New agents inherit this: resolve_cwd must not snap to process cwd."""
    from pathlib import Path

    other = tmp_path / "process-cwd"
    other.mkdir()
    project = tmp_path / "chip-project"
    project.mkdir()
    monkeypatch.chdir(other)

    for aid in list_agents():
        pair = get_agent(aid)
        assert pair is not None, aid
        got = Path(pair[1].resolve_cwd(str(project))).resolve()
        assert got == project.resolve(), f"{aid} ignored project chip → {got}"


def test_kernel_rejects_adapter_cwd_from_another_project(tmp_path, monkeypatch):
    """CH-000164: adapter/resume must not move execute() into Cuttle."""
    import os
    from pathlib import Path

    from api.agent_harness import kernel
    from api.agent_harness.types import AgentManifest, AgentResult

    cuttle = tmp_path / "Cuttle"
    demo = tmp_path / "DemoGame"
    cuttle.mkdir()
    demo.mkdir()
    monkeypatch.chdir(cuttle)
    seen = {"cwd": None}

    class _PinToCuttle:
        def available(self):
            return True

        def resolve_cwd(self, project_path: str) -> str:
            return str(cuttle)

        def load_resume(self, cwd, chat_session_id):
            return None

        def save_resume(self, cwd, chat_session_id, cli_session_id):
            pass

        def clear_resume(self, cwd, chat_session_id):
            pass

        async def execute(self, prompt, **kwargs):
            seen["cwd"] = kwargs.get("cwd")
            return AgentResult(success=True, output="pong")

    manifest = AgentManifest(
        id="fakeprobe",
        label="Fake",
        slash="/fakeprobe",
        resume=True,
        install_hint="n/a",
    )
    monkeypatch.setattr(
        kernel, "get_agent", lambda aid, project_path=None: (manifest, _PinToCuttle())
    )
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

    out = kernel.run_agent_web_command("fakeprobe", "hi", 164, project_path=str(demo))
    assert out.get("response") == "pong"
    assert Path(seen["cwd"]).resolve() == demo.resolve()
    assert Path(seen["cwd"]).resolve() != Path(os.getcwd()).resolve()

