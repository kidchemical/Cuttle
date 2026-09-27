"""Cheap completion helper — one HTTP implementation for titles/commits/router."""

from api.llm_complete import complete


def test_complete_openai_first(monkeypatch):
    monkeypatch.setattr(
        "api.llm_complete._via_openai",
        lambda *a, **k: "  hello from openai  ",
    )
    monkeypatch.setattr(
        "api.llm_complete._via_anthropic",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")),
    )
    assert complete(user="hi", providers=("openai",)) == "hello from openai"


def test_complete_strips_think_blocks(monkeypatch):
    monkeypatch.setattr(
        "api.llm_complete._via_local",
        lambda *a, **k: "<think>secret</think>\nTitle here",
    )
    assert complete(user="x", providers=("local",)) == "Title here"


def test_complete_local_mode_skips_cloud(monkeypatch):
    monkeypatch.setattr(
        "api.llm_complete._via_openai",
        lambda *a, **k: "cloud",
    )
    monkeypatch.setattr(
        "api.llm_complete._via_local",
        lambda *a, **k: "on device",
    )
    assert complete(user="x", inference_mode="local") == "on device"
