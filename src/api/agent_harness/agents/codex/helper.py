"""Ephemeral Codex text helpers with no workspace environments or tools."""
import json
import os
from pathlib import Path
import tomllib
from api.agent_harness.helper import HelperResult, helper_env, helper_process
from .model_catalog import list_codex_catalog_models
from scripts.utilities.codex_cli_tool import codex_executable


def models(refresh=False):
    return list_codex_catalog_models(refresh=refresh)


def config(auth):
    values = {"project_doc_max_bytes": 0, "web_search": "disabled", "mcp_servers": {},
              "tools.update_plan.enabled": False, "tools.experimental_request_user_input.enabled": False,
              "developer_instructions": "Return only the requested text. Do not use tools.",
              "agents.enabled": False}
    for name in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent",
                 "multi_agent_v2", "browser_use", "computer_use", "image_generation", "view_image",
                 "js_repl", "code_mode", "code_mode_host", "memories", "memory_tool", "skill_search",
                 "tool_suggest", "sleep_tool", "current_time_reminder", "stable_environment_tools"):
        values["features." + name] = False
    # TOML maps merge across config layers; disable native MCP entries explicitly.
    native = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")) / "config.toml"
    if native.is_file():
        with native.open("rb") as stream: saved = tomllib.load(stream)
        for name in saved.get("mcp_servers", {}):
            values['mcp_servers.' + json.dumps(name) + '.enabled'] = False
    if auth == "subscription": values["forced_login_method"] = "chatgpt"
    return values


def generate(request):
    exe = codex_executable()
    if not exe: raise RuntimeError("Codex is not installed; run codex login after installing it")
    settings = config(request.auth)
    args = [exe, "app-server"]
    for key, value in settings.items():
        # Only empty maps, strings, numbers and booleans are used here (valid TOML).
        encoded = "{}" if value == {} else json.dumps(value)
        args += ["-c", key + "=" + encoded]
    with helper_process(args, request, helper_env()) as (process, cwd):
        sequence = 0
        deferred = []
        def rpc(method, params):
            nonlocal sequence
            sequence += 1
            process.send({"id": sequence, "method": method, "params": params})
            while True:
                event = process.next()
                if event.get("id") == sequence and "method" not in event:
                    if "error" in event: raise RuntimeError("Codex helper protocol or authentication failed; update the CLI and check codex login")
                    return event.get("result") or {}
                if "id" in event and "method" in event:
                    raise RuntimeError("Codex requested an unsupported helper operation")
                deferred.append(event)
        rpc("initialize", {"clientInfo": {"name": "cuttle_helpers", "version": "1"}, "capabilities": {"experimentalApi": True}})
        process.send({"method": "initialized", "params": {}})
        account = rpc("account/read", {"refreshToken": False}).get("account") or {}
        if request.auth == "subscription" and str(account.get("type", "")).lower() not in ("chatgpt", "chatgptauthtokens"):
            raise RuntimeError("Codex helper needs a ChatGPT login; run codex login")
        thread = rpc("thread/start", {"cwd": cwd, "model": request.model or None,
            "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never",
            "environments": [], "dynamicTools": [], "baseInstructions": (request.system or "Return only the requested text.") + f"\nKeep the final answer within approximately {request.max_tokens} tokens.",
            "developerInstructions": "Return only the requested text. Tools are unavailable.", "config": settings})
        if thread.get("thread", {}).get("environments") != []:
            raise RuntimeError("Codex CLI cannot confirm an environment-free helper; update Codex")
        tid = thread["thread"]["id"]
        deferred.clear()
        params = {"threadId": tid, "input": [{"type": "text", "text": request.user}], "environments": []}
        if request.effort: params["effort"] = request.effort
        if request.json_object: params["outputSchema"] = {"type": "object", "additionalProperties": True}
        rpc("turn/start", params)
        text, usage = [], {}
        while True:
            event = deferred.pop(0) if deferred else process.next()
            if "id" in event and "method" in event:
                raise RuntimeError("Codex requested a tool during a text-only helper")
            method, data = event.get("method"), event.get("params") or {}
            if method == "item/completed":
                item = data.get("item") or {}
                if item.get("type") == "agentMessage": text.append(item.get("text") or "")
                elif item.get("type") not in ("userMessage", "reasoning"):
                    raise RuntimeError("Codex helper attempted a non-text operation")
            elif method == "thread/tokenUsage/updated":
                total = (data.get("tokenUsage") or {}).get("total") or {}
                usage = {"input_tokens": total.get("inputTokens", 0), "output_tokens": total.get("outputTokens", 0)}
            elif method == "turn/completed":
                if (data.get("turn") or {}).get("status") != "completed":
                    raise RuntimeError("Codex helper did not complete; check CLI login and plan allowance")
                return HelperResult("\n".join(text).strip(), request.model, usage)
