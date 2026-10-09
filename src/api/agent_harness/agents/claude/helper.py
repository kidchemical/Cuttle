"""One-shot Claude text helpers using the existing CLI login and model catalog."""
import time
from dataclasses import replace

from api.agent_harness.helper import HelperResult, helper_env, helper_process
from .model_catalog import list_claude_catalog_models
from scripts.utilities.claude_cli_tool import claude_executable


def models(refresh=False):
    return list_claude_catalog_models(refresh=refresh)


def generate(request):
    exe = claude_executable()
    if not exe: raise RuntimeError("Claude Code is not installed; run claude auth login after installing it")
    env = helper_env()
    env["CLAUDE_CODE_SAFE_MODE"] = "1"
    deadline = time.monotonic() + request.timeout
    if request.auth == "subscription":
        # Metadata only: never send a prompt or alter the native login store.
        check = replace(request, timeout=min(10, request.timeout))
        with helper_process([exe, "auth", "status", "--json"], check, env) as (process, _):
            status = process.next()
        if not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
            raise RuntimeError("Claude helper needs a subscription login; run claude auth login and select Claude.ai")
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise TimeoutError("Claude helper authentication timed out")
    request = replace(request, timeout=remaining)
    # --bare skips OAuth too, so it cannot be used for subscription helpers.
    args = [exe, "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}', "--disable-slash-commands",
            "--no-session-persistence", "--setting-sources", "", "--permission-mode", "dontAsk",
            "--settings", '{"disableAllHooks":true}',
            "--system-prompt-file", "{helper_system_prompt}"]
    if request.model: args += ["--model", request.model]
    if request.effort: args += ["--effort", request.effort]
    user = request.user
    if request.json_object: user += "\nReturn one valid JSON object, without Markdown fences."
    with helper_process(args, request, env) as (process, _):
        process.proc.stdin.write(user.encode()); process.proc.stdin.close()
        while True:
            event = process.next()
            if event.get("type") == "result" or "result" in event:
                if event.get("is_error") or event.get("subtype", "success") != "success":
                    raise RuntimeError("Claude helper failed; check CLI login and plan allowance")
                return HelperResult(str(event.get("result") or ""), request.model, event.get("usage") or {})
