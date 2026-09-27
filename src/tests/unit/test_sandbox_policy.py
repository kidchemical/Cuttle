"""Tests for sandbox allow/deny evaluation."""

from api.sandbox_policy import sandbox_evaluate_tool_node


def test_no_sandbox_allows_all_tools():
    ok, reason = sandbox_evaluate_tool_node("tool-remote-agent", None, [], [])
    assert ok is True
    assert reason is None


def test_allowlist_blocks_unknown_tool():
    allowed = {"tool-screenshot"}
    ok, reason = sandbox_evaluate_tool_node("tool-remote-agent", allowed, [], [])
    assert ok is False
    assert "allowed_tools" in (reason or "")


def test_explicit_deny_overrides_allowlist():
    allowed = {"tool-remote-agent", "tool-screenshot"}
    ok, reason = sandbox_evaluate_tool_node(
        "tool-remote-agent", allowed, ["tool-remote-agent"], []
    )
    assert ok is False
    assert "denied_tools" in (reason or "")


def test_prefix_deny():
    allowed = {"tool-mcp-generic"}
    ok, reason = sandbox_evaluate_tool_node(
        "tool-mcp-generic", allowed, [], ["tool-mcp"]
    )
    assert ok is False
    assert "prefix" in (reason or "").lower()


def test_non_tool_nodes_not_filtered():
    allowed = {"tool-screenshot"}
    ok, reason = sandbox_evaluate_tool_node("llm-anthropic", allowed, [], ["tool-"])
    assert ok is True
