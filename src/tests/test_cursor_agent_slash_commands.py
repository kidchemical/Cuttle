"""Tests for Cursor Agent slash commands (/model, /plan, …) in Cuttle chat."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from api.cursor_agent_commands import (
    format_cursor_usage_markdown,
    handle_cursor_agent_slash,
    parse_cursor_agent_slash,
)
from scripts.utilities.cursor_cli_tool import (
    _cursor_agent_cli_option_args,
    _format_cursor_agent_run_log_note,
    _wrap_cursor_agent_reply,
)
from scripts.utilities.cursor_cli_session_store import (
    append_cursor_run_meta,
    clear_cursor_resume_id,
    format_recent_cursor_runs_markdown,
    load_cursor_agent_options,
    load_cursor_resume_id,
    save_cursor_resume_id,
    update_cursor_agent_options,
)


def test_parse_cursor_agent_slash_basic():
    assert parse_cursor_agent_slash("/model") == ("model", "")
    assert parse_cursor_agent_slash("/model auto") == ("model", "auto")
    assert parse_cursor_agent_slash("/plan redesign auth") == ("plan", "redesign auth")
    assert parse_cursor_agent_slash("/ask") == ("ask", "")
    assert parse_cursor_agent_slash("/clear") == ("clear", "")
    assert parse_cursor_agent_slash("/new-chat") == ("new-chat", "")
    assert parse_cursor_agent_slash("/usage") == ("usage", "")
    assert parse_cursor_agent_slash("/usage now") == ("usage", "now")
    assert parse_cursor_agent_slash("hello") is None
    assert parse_cursor_agent_slash("/pipeline Foo") is None


def test_peel_user_request_and_model_resolve():
    from api.agent_harness.agents.cursor.adapter import (
        _peel_user_request,
        _resolve_cursor_model,
    )

    wrapped = (
        "<cuttle_context>\nrules here\n</cuttle_context>\n\n"
        "## User request\n"
        "/model cursor-grok-4.6-medium-fast what model are you?"
    )
    assert (
        _peel_user_request(wrapped)
        == "/model cursor-grok-4.6-medium-fast what model are you?"
    )
    assert _peel_user_request("just a prompt") == "just a prompt"

    # Policy order (CH-000419): this-turn slash → per-chat session pin →
    # explicit router override → starred → Auto. The user's explicit session
    # pin beats a system-level router override.
    assert (
        _resolve_cursor_model(
            slash_preferred="cursor-grok-4.6-high",
            session_model="composer-2.5",
            kernel_model="auto",
        )
        == "cursor-grok-4.6-high"
    )
    assert (
        _resolve_cursor_model(
            slash_preferred=None,
            session_model="cursor-grok-4.6-medium-fast",
            kernel_model="auto",
        )
        == "cursor-grok-4.6-medium-fast"
    )
    assert (
        _resolve_cursor_model(
            slash_preferred=None,
            session_model="cursor-grok-4.6-medium-fast",
            kernel_model="claude-opus-5-thinking-high",
        )
        == "cursor-grok-4.6-medium-fast"
    )
    assert (
        _resolve_cursor_model(
            slash_preferred=None,
            session_model=None,
            kernel_model="claude-opus-5-thinking-high",
        )
        == "claude-opus-5-thinking-high"
    )
    assert (
        _resolve_cursor_model(
            slash_preferred=None, session_model=None, kernel_model="auto"
        )
        == "auto"
    )


def test_cursor_adapter_peels_compiled_model_slash(tmp_path, monkeypatch):
    """Context Compiler envelopes must not hide `/model <id> <prompt>`."""
    import asyncio

    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    from api.agent_harness.agents.cursor.adapter import build_adapter

    seen = {}

    def _fake_oneline(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["model"] = kwargs.get("model")
        return "pong"

    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_tool._cursor_agent_oneline_prompt", _fake_oneline
    )
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_tool.handle_cursor_cli_command", lambda *_a, **_k: "meta"
    )

    ad = build_adapter()
    cwd = str(tmp_path)
    sid = "382"
    compiled = (
        "<cuttle_context>\nhub rules\n</cuttle_context>\n\n"
        "## User request\n"
        "/model cursor-grok-4.6-medium-fast what model are you?"
    )
    result = asyncio.run(
        ad.execute(
            compiled,
            cwd=cwd,
            resume=None,
            model="auto",
            chat_session_id=sid,
        )
    )
    assert result.success
    assert seen.get("model") == "cursor-grok-4.6-medium-fast"
    assert "what model are you?" in (seen.get("prompt") or "")
    assert "/model" not in (seen.get("prompt") or "").split("## User request")[-1]
    opts = load_cursor_agent_options(cwd, sid)
    assert opts.get("model") == "cursor-grok-4.6-medium-fast"


def test_cursor_agent_cli_option_args():
    assert _cursor_agent_cli_option_args() == []
    assert _cursor_agent_cli_option_args(model="auto") == ["--model", "auto"]
    assert _cursor_agent_cli_option_args(mode="plan") == ["--mode", "plan"]
    assert _cursor_agent_cli_option_args(mode="ask", sandbox="enabled") == [
        "--mode",
        "ask",
        "--sandbox",
        "enabled",
    ]
    assert _cursor_agent_cli_option_args(mode="nope") == []


def test_cursor_run_log_note_and_wrap():
    note = _format_cursor_agent_run_log_note(
        requested_model="auto",
        reported_model="Auto",
        request_id="58b17f6d-2798-48ab-b01e-b30630274366",
        usage={"inputTokens": 13574, "outputTokens": 12},
        cwd=r"C:\Projects\Cuttle",
    )
    assert "requested=auto" in note
    assert "reported=Auto" in note
    assert "58b17f6d" in note
    assert "tok" in note
    assert "cwd=" in note

    wrapped = _wrap_cursor_agent_reply(
        "pong",
        label="agent",
        cwd=r"C:\Projects\Cuttle",
        requested_model="auto",
        reported_model="Auto",
        request_id="abcd1234-eeee-ffff-aaaa-bbbbbbbbbbbb",
        usage={"inputTokens": 100, "outputTokens": 5},
    )
    assert wrapped == "pong"
    assert "Cursor run" not in wrapped
    assert "Cursor Agent" not in wrapped


def test_session_store_options_preserve_resume(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    cwd = str(tmp_path / "proj")
    (tmp_path / "proj").mkdir()
    sid = "db_session_42"
    save_cursor_resume_id(cwd, sid, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    update_cursor_agent_options(cwd, sid, model="auto", mode="plan")
    opts = load_cursor_agent_options(cwd, sid)
    assert opts.get("resume_id") == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert opts.get("model") == "auto"
    assert opts.get("mode") == "plan"
    assert load_cursor_resume_id(cwd, sid) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    clear_cursor_resume_id(cwd, sid)
    opts2 = load_cursor_agent_options(cwd, sid)
    assert "resume_id" not in opts2
    assert opts2.get("model") == "auto"
    assert opts2.get("mode") == "plan"
    assert load_cursor_resume_id(cwd, sid) is None


def test_session_store_accepts_numeric_chat_id(tmp_path, monkeypatch):
    """CH-000155: auth chats pass the int DB id, and the save crashed on `.strip()`.

    Every `/cursor` turn then ran without `--resume`, so the agent had no memory of
    its own chat and went looking for history in whatever session was touched last.
    """
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    cwd = str(tmp_path / "proj")
    (tmp_path / "proj").mkdir()
    rid = "11111111-2222-3333-4444-555555555555"

    save_cursor_resume_id(cwd, 155, rid)

    # Readable through every id shape the chat surfaces use for one session.
    assert load_cursor_resume_id(cwd, 155) == rid
    assert load_cursor_resume_id(cwd, "155") == rid
    assert load_cursor_resume_id(cwd, "db_session_155") == rid
    # Run meta already tolerated ints; both writers must share one entry.
    append_cursor_run_meta(cwd, 155, {"requested_model": "auto"})
    assert load_cursor_resume_id(cwd, 155) == rid


def test_append_cursor_run_meta_and_markdown(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    cwd = str(tmp_path / "proj")
    (tmp_path / "proj").mkdir()
    sid = "db_session_9"
    append_cursor_run_meta(
        cwd,
        sid,
        {
            "requested_model": "auto",
            "reported_model": "Auto",
            "request_id": "11111111-2222-3333-4444-555555555555",
            "usage": {"inputTokens": 10, "outputTokens": 2},
            "ts": 1700000000.0,
        },
    )
    append_cursor_run_meta(
        cwd,
        sid,
        {
            "requested_model": "composer-2.5",
            "reported_model": "composer-2.5",
            "request_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "usage": {"inputTokens": 20, "outputTokens": 4},
            "ts": 1700001000.0,
        },
    )
    opts = load_cursor_agent_options(cwd, sid)
    assert opts.get("last_reported_model") == "composer-2.5"
    assert len(opts.get("recent_runs") or []) == 2
    md = format_recent_cursor_runs_markdown(opts, limit=5)
    assert "Recent Cursor runs" in md
    assert "composer-2.5" in md
    assert "Auto" in md or "auto" in md


def test_handle_model_set_and_list(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    cwd = str(tmp_path)
    sid = "99"

    append_cursor_run_meta(
        cwd,
        sid,
        {
            "requested_model": "auto",
            "reported_model": "Auto",
            "request_id": "99999999-9999-9999-9999-999999999999",
            "ts": 1700000000.0,
        },
    )

    with patch(
        "api.cursor_agent_commands.list_cursor_agent_models",
        return_value=[{"id": "auto", "label": "Auto", "current": True}],
    ):
        listed = handle_cursor_agent_slash("/model", cwd=cwd, chat_session_id=sid)
    assert listed and listed["action"] == "reply"
    assert "auto" in listed["message"].lower()
    assert "Recent Cursor runs" in listed["message"]
    assert "Session preferred" in listed["message"]

    set_r = handle_cursor_agent_slash(
        "/model claude-sonnet-5-thinking-high", cwd=cwd, chat_session_id=sid
    )
    assert set_r and set_r["action"] == "reply"
    assert set_r.get("ui") == "system"
    assert str(set_r.get("notice") or "").startswith("⚡ Model:")
    assert "claude-sonnet-5-thinking-high" in set_r["message"]
    assert set_r.get("preferred_model") == "claude-sonnet-5-thinking-high"
    opts = load_cursor_agent_options(cwd, sid)
    assert opts.get("model") == "claude-sonnet-5-thinking-high"

    # `/model <id> <prompt>` sets the model and runs the remainder (like /plan).
    set_and_run = handle_cursor_agent_slash(
        "/model claude-4.6-sonnet-medium-thinking fix the doorframe",
        cwd=cwd,
        chat_session_id=sid,
    )
    assert set_and_run and set_and_run["action"] == "run"
    assert set_and_run["prompt"] == "fix the doorframe"
    assert set_and_run.get("preferred_model") == "claude-4.6-sonnet-medium-thinking"
    assert (
        load_cursor_agent_options(cwd, sid).get("model")
        == "claude-4.6-sonnet-medium-thinking"
    )


def test_handle_model_refresh(monkeypatch):
    monkeypatch.setattr(
        "api.cursor_agent_commands.refresh_cursor_agent_models",
        lambda: {
            "models": [{"id": "auto", "label": "Auto"}, {"id": "composer-2", "label": "Composer"}],
            "count": 2,
            "source": "cli_refresh",
            "error": None,
        },
    )
    out = handle_cursor_agent_slash("/model refresh", cwd=".", chat_session_id="1")
    assert out and out["action"] == "reply"
    assert "2" in out["message"]
    assert "palette" in out["message"].lower()


def test_cursor_model_refresh_palette_wired():
    js = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
    text = js.read_text(encoding="utf-8")
    assert "/model refresh" in text
    # Staged as cursor-cmd (not instant cursorRefresh on pick).
    assert "category: 'cursor-cmd'" in text
    assert "cursorRefresh: true" not in text
    assert "qs.set('refresh', '1')" in text
    assert "loadCursorAgentModelsForPalette(true, { refresh: true })" in text


def test_handle_plan_ask_agent_and_clear(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    cwd = str(tmp_path)
    sid = "7"
    save_cursor_resume_id(cwd, sid, "11111111-2222-3333-4444-555555555555")

    plan_only = handle_cursor_agent_slash("/plan", cwd=cwd, chat_session_id=sid)
    assert plan_only and plan_only["action"] == "reply"
    assert load_cursor_agent_options(cwd, sid).get("mode") == "plan"

    plan_run = handle_cursor_agent_slash(
        "/plan redesign auth", cwd=cwd, chat_session_id=sid
    )
    assert plan_run == {"action": "run", "prompt": "redesign auth", "mode": "plan"}

    ask_run = handle_cursor_agent_slash("/ask what is this?", cwd=cwd, chat_session_id=sid)
    assert ask_run == {"action": "run", "prompt": "what is this?", "mode": "ask"}
    assert load_cursor_agent_options(cwd, sid).get("mode") == "ask"

    agent_reset = handle_cursor_agent_slash("/agent", cwd=cwd, chat_session_id=sid)
    assert agent_reset and agent_reset["action"] == "reply"
    assert "mode" not in load_cursor_agent_options(cwd, sid)

    cleared = handle_cursor_agent_slash("/clear", cwd=cwd, chat_session_id=sid)
    assert cleared and cleared["action"] == "reply"
    assert load_cursor_resume_id(cwd, sid) is None


def test_format_cursor_usage_markdown_included_plan():
    md = format_cursor_usage_markdown(
        {
            "success": True,
            "period": {
                "billingCycleStart": "1787335890000",
                "billingCycleEnd": "1790014290000",
                "displayMessage": "You've used 39% of your included usage",
                "autoModelSelectedDisplayMessage": "You've used 2% of your included total usage",
                "namedModelSelectedDisplayMessage": "You've used 17% of your included API usage",
            },
            "plan_info": {
                "planName": "Pro",
                "price": "$20/mo",
                "includedAmountCents": 2000,
            },
            "plan_usage": {
                "totalSpend": 772,
                "includedSpend": 772,
                "remaining": 1228,
                "limit": 2000,
                # CLI-aligned meters (can diverge from dollar spend %).
                "totalPercentUsed": 3.0,
                "autoPercentUsed": 3.0,
                "apiPercentUsed": 0.0,
            },
            "hard_limit": {"noUsageBasedAllowed": True},
            "stripe": {"membershipType": "pro", "subscriptionStatus": "active"},
            "aggregations": [
                {
                    "modelIntent": "gpt-5.6-sol-medium",
                    "outputTokens": "16647",
                    "totalCents": 390.3,
                },
                {"modelIntent": "default", "tier": 0},
            ],
        }
    )
    assert "Cursor — usage" in md
    assert "Pro" in md
    assert "<cuttle_meters>" in md
    assert '"label":"Included"' in md.replace(" ", "")
    assert '"pct":3' in md.replace(" ", "")
    assert "<vega>" not in md
    assert "$7.72" in md
    assert "$20.00" in md
    assert "$12.28" in md
    assert "gpt-5.6-sol-medium" in md
    assert "on-demand is off" in md.lower() or "Off" in md


def test_format_cursor_usage_prefers_cli_pct_over_dollar_display_message():
    """Dollar displayMessage can say 60% while CLI Included is ~3%."""
    md = format_cursor_usage_markdown(
        {
            "success": True,
            "period": {
                "billingCycleStart": "1790014290000",
                "billingCycleEnd": "1792606290000",
                "displayMessage": "You've used 60% of your included usage",
                "autoModelSelectedDisplayMessage": "You've used 3% of your included total usage",
                "namedModelSelectedDisplayMessage": "You've used 0% of your included API usage",
            },
            "plan_info": {"planName": "Pro", "price": "$20/mo", "includedAmountCents": 2000},
            "plan_usage": {
                "totalSpend": 1206,
                "includedSpend": 1206,
                "remaining": 794,
                "limit": 2000,
                "autoPercentUsed": 2.68,
                "apiPercentUsed": 0,
                "totalPercentUsed": 2.6146341463414635,
            },
            "hard_limit": {"noUsageBasedAllowed": True},
            "stripe": {"membershipType": "pro", "subscriptionStatus": "active"},
            "aggregations": [],
        }
    )
    assert "<cuttle_meters>" in md
    assert '"pct":2.61' in md.replace(" ", "") or '"pct":2.62' in md.replace(" ", "")
    assert "Included: 60%" not in md
    assert "<vega>" not in md
    assert "Compute $" in md
    assert "$12.06" in md


def test_cuttle_meters_tag_wired_in_chat_page():
    js = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_page.js"
    mod = Path(__file__).resolve().parents[1] / "web" / "js" / "chat_messages.js"
    css = Path(__file__).resolve().parents[1] / "web" / "css" / "chat_page.css"
    text = js.read_text(encoding="utf-8")
    owned = mod.read_text(encoding="utf-8")
    styles = css.read_text(encoding="utf-8")
    # Meters planning lives in the owned Messages module (Phase 3 Slice 8C);
    # the page stays wired through the structured-block planning calls.
    assert "extractTailStructuredBlocks" in text
    assert "restoreStructuredBlocks(" in text
    # Slice 8D redacts code/link from the bulk pass and restores them after
    # forms/buttons (pre-change pass order); the page stays wired through
    # the structured-block restore protocol either way.
    assert "structuredBlocks, { code: [], link: [] }" in text
    assert "structuredBlocks.code.length" in text
    assert "structuredBlocks.link.length" in text
    assert "cuttle_meters" in owned
    assert "CUTTLE_METERS_" in owned
    assert "cuttle-meters" in styles
    assert "cuttle-meter-fill" in styles
    # Vega no longer forced into ui-card / dark theme for opaque analytics look.
    assert 'theme: document.body.classList.contains(\'light-mode\') ? \'latimes\' : \'dark\'' not in text
    assert 'class="ui-card vega-wrap"' not in text


def test_format_cursor_usage_markdown_error():
    md = format_cursor_usage_markdown(
        {"success": False, "error": "Could not find a Cursor IDE login session"}
    )
    assert md.startswith("❌")
    assert "login session" in md


def test_handle_cursor_usage_slash(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    cwd = str(tmp_path / "proj")
    (tmp_path / "proj").mkdir()
    sid = "db_session_usage"

    fake = {
        "success": True,
        "period": {
            "billingCycleStart": "1787335890000",
            "billingCycleEnd": "1790014290000",
            "displayMessage": "You've used 10% of your included usage",
        },
        "plan_info": {"planName": "Pro", "price": "$20/mo"},
        "plan_usage": {
            "totalSpend": 200,
            "includedSpend": 200,
            "remaining": 1800,
            "limit": 2000,
        },
        "hard_limit": {"noUsageBasedAllowed": True},
        "stripe": {"subscriptionStatus": "active"},
        "aggregations": [],
    }
    with patch(
        "api.cursor_agent_commands.fetch_cursor_account_usage",
        return_value=fake,
    ):
        out = handle_cursor_agent_slash("/usage", cwd=cwd, chat_session_id=sid)
    assert out and out["action"] == "reply"
    assert "$2.00" in out["message"]
    assert "Pro" in out["message"]


def test_resume_binding_shares_src_and_repo_root(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    from scripts.utilities.cursor_cli_session_store import (
        apply_cursor_resume_workspace,
        cwd_alias_candidates,
        load_cursor_resume_binding,
        save_cursor_resume_id,
    )

    root = tmp_path / "Cuttle"
    src = root / "src"
    src.mkdir(parents=True)
    rid = "0b92786a-7a6d-4adf-9a50-8e96b1045861"
    sid = "db_session_107"
    save_cursor_resume_id(str(root), sid, rid)

    aliases = cwd_alias_candidates(str(src))
    assert str(root.resolve()) in aliases
    assert str(src.resolve()) in aliases

    binding = load_cursor_resume_binding(str(src), sid)
    assert binding.get("resume_id") == rid
    assert Path(binding.get("workspace")).resolve() == root.resolve()

    ws, got = apply_cursor_resume_workspace(str(src), sid)
    assert got == rid
    assert Path(ws).resolve() == root.resolve()

    clear_cursor_resume_id(str(src), sid)
    assert load_cursor_resume_id(str(root), sid) is None
    assert load_cursor_resume_id(str(src), sid) is None


def test_resume_binding_shares_source_and_unity_parent(tmp_path, monkeypatch):
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    from scripts.utilities.cursor_cli_session_store import (
        cwd_alias_candidates,
        load_cursor_resume_binding,
        prepare_cursor_agent_workspace,
        save_cursor_resume_id,
    )

    root = tmp_path / "DemoGame"
    source = root / "source"
    source.mkdir(parents=True)
    import subprocess

    subprocess.run(
        ["git", "init"],
        cwd=str(source),
        check=True,
        capture_output=True,
    )
    rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    sid = "db_session_108"

    aliases = cwd_alias_candidates(str(root))
    assert str(source.resolve()) in aliases

    # New chat: no resume yet → workspace is nested git (source/)
    ws, got = prepare_cursor_agent_workspace(str(root), sid)
    assert got is None
    assert Path(ws).resolve() == source.resolve()

    save_cursor_resume_id(str(source), sid, rid)
    binding = load_cursor_resume_binding(str(root), sid)
    assert binding.get("resume_id") == rid
    assert Path(binding.get("workspace")).resolve() == source.resolve()


def test_resume_binding_does_not_follow_other_project(tmp_path, monkeypatch):
    """CH-000164: a Cuttle resume must not pin --workspace when the chip is EP."""
    map_file = tmp_path / "cursor_cli_session_map.json"
    monkeypatch.setattr(
        "scripts.utilities.cursor_cli_session_store._map_file",
        lambda: map_file,
    )
    from scripts.utilities.cursor_cli_session_store import (
        load_cursor_resume_binding,
        load_cursor_resume_id,
        prepare_cursor_agent_workspace,
        save_cursor_resume_id,
    )

    cuttle = tmp_path / "Cuttle"
    demo = tmp_path / "DemoGame"
    cuttle.mkdir()
    demo.mkdir()
    rid = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    sid = "db_session_164"
    save_cursor_resume_id(str(cuttle), sid, rid)

    assert load_cursor_resume_id(str(cuttle), sid) == rid
    assert load_cursor_resume_binding(str(demo), sid) == {}
    assert load_cursor_resume_id(str(demo), sid) is None

    ws, got = prepare_cursor_agent_workspace(str(demo), sid)
    assert got is None
    assert Path(ws).resolve() == demo.resolve()
