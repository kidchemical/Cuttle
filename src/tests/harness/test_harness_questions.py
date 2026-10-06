"""Native input recovery without real CLIs, user data or model calls."""
import asyncio
import json
import os
import stat
import sys

import pytest

from api.agent_harness.questions import QuestionBridge, normalize_question_payload, question_to_form
from api.action_forms import normalize_action_form_spec, rewrite_action_forms

PAYLOAD = {"questions": [{"id": "scope", "question": "Which scope?", "options": [
    {"label": "Small", "description": "Small change"}, {"label": "Full"}]}]}


def spec_from(text):
    return json.loads(text.split("<cuttle_action_form>", 1)[1].split("</cuttle_action_form>", 1)[0])


def test_free_text_and_multi_question_recovery():
    bridge = QuestionBridge()
    bridge.capture(PAYLOAD)
    bridge.capture(json.dumps(PAYLOAD))  # same tool, completed event
    bridge.capture({"questions": [{"title": "What did you observe?"}, {
        "question": "Which areas?", "multiSelect": True, "options": ["UI", "API"]}]})
    spec = spec_from(bridge.render("Findings"))
    assert spec["resume"] is True
    assert [f["type"] for f in spec["fields"]] == ["radio", "text", "checkboxes"]
    assert len({f["id"] for f in spec["fields"]}) == 3
    assert normalize_action_form_spec(spec)
    assert all("action" not in f for f in spec["fields"])


def test_side_effect_or_invalid_form_does_not_hide_native_question():
    bridge = QuestionBridge()
    bridge.capture(PAYLOAD)
    existing = '<cuttle_action_form>{"resume":true,"title":"Which scope?","options":[{"id":"yes","label":"Yes","action":"git.push"}]}</cuttle_action_form>'
    assert bridge.render(existing).count("<cuttle_action_form>") == 2
    assert bridge.render('<cuttle_action_form>{broken}</cuttle_action_form>').count("<cuttle_action_form>") == 2
    rendered = bridge.render("Findings")
    assert bridge.render(rendered) == rendered


def test_malformed_question_is_visible_and_never_omitted():
    bridge = QuestionBridge()
    bridge.capture({"questions": [{"question": "Valid"}, {"options": []}]})
    assert bridge.pending
    assert "could not decode" in bridge.render("")
    assert "No answer was supplied" in bridge.render("")
    assert "<cuttle_action_form>" not in bridge.render("")


def test_card_rewrite_keeps_restart_recoverable_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "question-tests")
    bridge = QuestionBridge()
    bridge.capture(PAYLOAD)
    monkeypatch.setattr("api.action_forms.resolve_session_project_path", lambda _sid: "")
    monkeypatch.setattr("api.project_actions._hmac_secret_cache", None)
    rewritten, count = rewrite_action_forms(bridge.render(""), session_id="question-test", project_path=str(tmp_path))
    assert count == 1
    assert "<cuttle_action_form_pending" in rewritten
    assert '"resume": true' in rewritten
    assert "Which scope?" in rewritten
    # A persisted pending card still suppresses an identical fallback card.
    assert bridge.render(rewritten) == rewritten


@pytest.mark.parametrize("vendor", ["codex", "muse", "opencode", "claude"])
def test_vendor_exported_question_payloads(vendor):
    if vendor == "codex":
        from scripts.utilities.codex_cli_tool import _parse_codex_jsonl
        raw = json.dumps({"type": "item.completed", "item": {
            "type": "toolCall", "name": "request_user_input", "arguments": json.dumps(PAYLOAD)}})
        output = _parse_codex_jsonl(raw)["output"]
    elif vendor == "muse":
        from scripts.utilities.muse_cli_tool import _parse_muse_jsonl
        raw = json.dumps({"payload_type": "tool.call", "payload": {
            "tool": "request_user_input", "args": json.dumps(PAYLOAD)}})
        output = _parse_muse_jsonl(raw)["output"]
    elif vendor == "opencode":
        from api.agent_harness.agents.opencode.adapter import _parse_opencode_stdout
        raw = json.dumps({"type": "tool_use", "part": {
            "tool": "question", "state": {"input": PAYLOAD, "status": "error"}}})
        output = _parse_opencode_stdout(raw)[0]
    else:
        from scripts.utilities.claude_cli_tool import _parse_claude_json
        raw = json.dumps({"type": "result", "result": "Question needs input", "permission_denials": [
            {"tool_name": "AskUserQuestion", "tool_input": PAYLOAD}]})
        output = _parse_claude_json(raw)["output"]
    assert spec_from(output)["title"] == "Which scope?"
    assert spec_from(output)["resume"] is True


# Emits a native RPC question and waits for a reply: reproduces the invisible
# picker without a real vendor process. A second process resumes the saved id.
FAKE_SERVER = r'''
import json, sys
vendor = VENDOR
question = PAYLOAD
def emit(obj):
    if vendor == "muse": obj["jsonrpc"] = "2.0"
    print(json.dumps(obj), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize": emit({"id":mid,"result":{}})
    elif method in ("thread/start", "thread/resume"):
        emit({"id":mid,"result":{"thread":{"id":"saved-session"}}})
    elif method in ("session/start", "session/resume"):
        emit({"id":mid,"result":{"session":{"sessionId":"saved-session"}}})
    elif method in ("session/setApprovalMode", "session/setModel"):
        emit({"id":mid,"result":{"status":"accepted"}})
    elif method == "turn/start":
        result = {"turn":{"id":"turn-1"}} if vendor == "codex" else {"turnId":"turn-1","status":"accepted"}
        emit({"id":mid,"result":result})
        prompt = msg["params"]["input"][0]["text"]
        if prompt == "go":
            if ITEM:
                emit({"method":"item/started","params":{"sessionId":"saved-session", "turnId":"turn-1", "item":{
                    "kind":"toolCall","tool":"request_user_input","args":json.dumps(question),"turnId":"turn-1"}}})
            else:
                emit({"id":100,"method":"item/tool/requestUserInput","params":question})
        else:
            if vendor == "codex":
                item={"type":"agentMessage","id":"a","text":"answer received: "+prompt}
                emit({"method":"item/completed","params":{"threadId":"saved-session","turnId":"turn-1","item":item}})
                emit({"method":"turn/completed","params":{"threadId":"saved-session","turn":{"id":"turn-1","status":"completed"}}})
            else:
                item={"kind":"agentMessage","itemId":"a","text":"answer received: "+prompt,"turnId":"turn-1"}
                emit({"method":"item/completed","params":{"sessionId":"saved-session","item":item}})
                emit({"method":"turn/completed","params":{"sessionId":"saved-session","turnId":"turn-1","terminal":"completed"}})
    elif mid == 100:
        # Deferral must not look like a submitted answer or an empty result.
        assert "error" in msg and "No user answer supplied" in msg["error"]["message"]
        with open(REPLY_FILE, "w") as f: json.dump(msg, f)
'''


def fake_bin(tmp_path, vendor, payload=PAYLOAD, item=False):
    path = tmp_path / vendor
    source = FAKE_SERVER.replace("vendor = VENDOR", f"vendor = {vendor!r}")
    source = source.replace("question = PAYLOAD", f"question = {payload!r}").replace("if ITEM:", f"if {item!r}:")
    source = source.replace("REPLY_FILE", repr(str(tmp_path / (vendor + "-reply.json"))))
    path.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.mark.skipif(os.name == "nt", reason="fake executables use POSIX shebangs")
@pytest.mark.parametrize("vendor,item", [("codex", False), ("muse", False), ("muse", True)])
def test_server_question_returns_card_and_next_turn_resumes(tmp_path, monkeypatch, vendor, item):
    if vendor == "codex":
        import scripts.utilities.codex_app_server_turn as runner
        monkeypatch.setattr(runner, "codex_executable", lambda: fake_bin(tmp_path, vendor, item=item))
        execute = runner.run_codex_turn_app_server
    else:
        import scripts.utilities.muse_serve_turn as runner
        monkeypatch.setattr(runner, "_which_muse_native", lambda: fake_bin(tmp_path, vendor, item=item))
        execute = runner.run_muse_turn_serve
    kwargs = dict(cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None, timeout=5)
    result = asyncio.run(execute("go", **kwargs))
    assert result["success"] and result["awaiting_input"]
    assert not result.get("fallback") and not result.get("cancelled")
    assert spec_from(result["output"])["title"] == "Which scope?"
    if not item:
        reply = json.loads((tmp_path / (vendor + "-reply.json")).read_text(encoding="utf-8"))
        assert "result" not in reply and "error" in reply
    saved = result[f"{vendor}_session_id"]
    assert saved == "saved-session"
    kwargs["resume"] = saved
    answer = "[form-selection] Small (opt_0)"
    resumed = asyncio.run(execute(answer, **kwargs))
    assert resumed["success"]
    assert resumed["output"] == "answer received: " + answer
    assert resumed[f"{vendor}_session_id"] == saved


@pytest.mark.skipif(os.name == "nt", reason="fake executables use POSIX shebangs")
@pytest.mark.parametrize("vendor", ["codex", "muse"])
def test_server_malformed_input_is_visible_without_retry(tmp_path, monkeypatch, vendor):
    if vendor == "codex":
        import scripts.utilities.codex_app_server_turn as runner
        monkeypatch.setattr(runner, "codex_executable", lambda: fake_bin(tmp_path, vendor, payload={"questions": []}))
        execute = runner.run_codex_turn_app_server
    else:
        import scripts.utilities.muse_serve_turn as runner
        monkeypatch.setattr(runner, "_which_muse_native", lambda: fake_bin(tmp_path, vendor, payload={"questions": []}))
        execute = runner.run_muse_turn_serve
    result = asyncio.run(execute("go", cwd=str(tmp_path), resume=None, model=None, reasoning_effort=None, timeout=5))
    assert result["success"] and not result["awaiting_input"]
    assert not result.get("fallback")
    assert "could not decode" in result["output"]


@pytest.mark.skipif(os.name == "nt", reason="fake executables use POSIX shebangs")
@pytest.mark.parametrize("vendor", ["codex", "muse", "opencode"])
def test_exec_question_stops_owned_process_without_timeout(tmp_path, monkeypatch, vendor):
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    if vendor == "codex":
        events = [{"type": "thread.started", "thread_id": sid}, {"type": "item.started", "item": {
            "type": "toolCall", "name": "request_user_input", "arguments": json.dumps(PAYLOAD)}}]
        import scripts.utilities.codex_cli_tool as mod
        monkeypatch.setattr(mod, "codex_executable", lambda: str(tmp_path / vendor))
        execute = mod.CodexCliTool().execute_prompt
    elif vendor == "muse":
        events = [{"stream": {"kind": "session", "id": sid}, "payload_type": "run.lifecycle.started"},
                  {"payload_type": "tool.call", "payload": {"tool": "request_user_input", "args": json.dumps(PAYLOAD)}}]
        import scripts.utilities.muse_cli_tool as mod
        monkeypatch.setattr(mod, "_which_muse_native", lambda: str(tmp_path / vendor))
        execute = mod.MuseCliTool(model="fake-model").execute_prompt
    else:
        events = [{"type": "tool_use", "sessionID": sid, "part": {
            "tool": "question", "state": {"input": PAYLOAD, "status": "running"}}}]
        from api.agent_harness.agents.opencode import adapter as mod
        monkeypatch.setattr(mod, "opencode_executable", lambda: str(tmp_path / vendor))
        monkeypatch.setattr("api.agent_harness.agent_defaults.get_starred_model", lambda _a: None)
        monkeypatch.setattr("api.agent_harness.agent_defaults.get_starred_effort", lambda _a: None)
        execute = mod.Adapter().execute
    path = tmp_path / vendor
    path.write_text(f"#!{sys.executable}\nimport json, time\nfor event in {events!r}:\n print(json.dumps(event), flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    kwargs = dict(cwd=str(tmp_path), timeout=5)
    if vendor == "opencode":
        kwargs.update(resume=None, model=None)
    result = asyncio.run(execute("go", **kwargs))
    if vendor == "opencode":
        assert result.success
        assert result.session_id == sid
        output = result.output
    else:
        assert result["success"] and not result.get("timed_out") and not result.get("cancelled")
        assert result[f"{vendor}_session_id"] == sid
        output = result["output"]
    assert spec_from(output)["resume"] is True


def test_recovered_free_text_form_submission_has_real_answer(monkeypatch):
    from api.action_forms import execute_action_form_submission, encode_form_fallback
    monkeypatch.setenv("CUTTLE_ACTION_HMAC_SECRET", "question-submission")
    monkeypatch.setattr("api.project_actions._hmac_secret_cache", None)
    monkeypatch.setattr("api.action_forms.resolve_session_project_path", lambda _sid: "")
    monkeypatch.setattr("api.action_forms.read_action_form_lock_from_history", lambda *a, **k: None)
    bridge = QuestionBridge()
    bridge.capture({"questions": [{"question": "What did you observe?"}]})
    spec = normalize_action_form_spec(spec_from(bridge.render("")))
    result = execute_action_form_submission(form_token=encode_form_fallback(spec),
        selection={"option": "submit", "fields": {"q1": "The buttons overlap"}}, session_id="question-test")
    assert result["success"] and result["resume"]
    assert result["answer_text"] == "[form-answers]\n- What did you observe?: The buttons overlap"
    cancelled = execute_action_form_submission(form_token=encode_form_fallback(spec),
        selection={"cancel": True}, session_id="question-test")
    assert not cancelled.get("resume") and not cancelled.get("answer_text")


@pytest.mark.parametrize("vendor", ["codex", "muse"])
def test_cancelled_exec_parse_does_not_recover_input(vendor):
    if vendor == "codex":
        from scripts.utilities.codex_cli_tool import _parse_codex_jsonl as parse
        event = {"type": "item.started", "item": {"name": "request_user_input", "input": PAYLOAD}}
    else:
        from scripts.utilities.muse_cli_tool import _parse_muse_jsonl as parse
        event = {"payload_type": "tool.call", "payload": {"tool": "request_user_input", "input": PAYLOAD}}
    assert "<cuttle_action_form>" not in parse(json.dumps(event), recover_questions=False)["output"]


def test_native_cancel_answer_is_not_card_dismissal():
    payload = normalize_question_payload({"questions": [{"question": "Which action?", "options": [
        {"id": "opt_1", "label": "Continue"}, {"id": "opt_1", "label": "Pause"},
        {"id": "cancel", "label": "Cancel operation"}]}]})
    spec = question_to_form(payload)
    assert len({o["id"] for o in spec["options"]}) == 3
    assert all(o["id"].lower() != "cancel" for o in spec["options"])
    assert spec["options"][-1]["label"] == "Cancel operation"


@pytest.mark.skipif(os.name == "nt", reason="fake executables use POSIX shebangs")
def test_claude_disables_native_picker_and_recovers_denied_question(tmp_path, monkeypatch):
    from scripts.utilities import claude_cli_tool as mod
    path = tmp_path / "claude"
    result = {"type": "result", "subtype": "success", "result": "Need your input", "session_id": "saved-session",
              "permission_denials": [{"tool_name": "AskUserQuestion", "tool_input": PAYLOAD}]}
    path.write_text(f"#!{sys.executable}\nimport json,sys\n"
                    "assert sys.argv[sys.argv.index('--disallowedTools')+1] == 'AskUserQuestion'\n"
                    "assert sys.argv[sys.argv.index('--disallowedTools')+2] == '--permission-mode'\n"
                    "assert sys.argv[-1] == 'go'\n"
                    f"print(json.dumps({result!r}), flush=True)\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(mod, "claude_executable", lambda: str(path))
    output = asyncio.run(mod.ClaudeCliTool().execute_prompt("go", cwd=str(tmp_path), timeout=5))
    assert output["success"]
    assert output["claude_session_id"] == "saved-session"
    assert spec_from(output["output"])["title"] == "Which scope?"


@pytest.mark.parametrize("vendor", ["codex", "muse"])
def test_partial_tool_start_waits_for_complete_question_arguments(vendor):
    if vendor == "codex":
        from scripts.utilities.codex_cli_tool import _capture_codex_question as capture
    else:
        from scripts.utilities.muse_cli_tool import _capture_muse_question as capture
    bridge = QuestionBridge()
    assert not capture(bridge, {"tool": "request_user_input", "args": ""}, complete=False)
    assert not bridge.pending
    assert capture(bridge, {"tool": "request_user_input", "args": json.dumps(PAYLOAD)}, complete=True)
    assert not bridge.invalid
    assert spec_from(bridge.render(""))["title"] == "Which scope?"
