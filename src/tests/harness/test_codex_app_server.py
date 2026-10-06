"""Offline tests for Codex app-server compact JSON-RPC helper."""

from __future__ import annotations

import json
import threading
from typing import List

import pytest

from scripts.utilities import codex_app_server as cas


class _FakePipe:
    def __init__(self, lines: List[bytes]):
        self._lines = list(lines)
        self._i = 0
        self.closed = False
        self.writes: List[bytes] = []

    def __iter__(self):
        return self

    def __next__(self):
        if self._i >= len(self._lines):
            raise StopIteration
        line = self._lines[self._i]
        self._i += 1
        return line

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, stdout_lines: List[bytes]):
        self.stdin = _FakePipe([])
        self.stdout = _FakePipe(stdout_lines)
        self.stderr = _FakePipe([])
        self._code = None

    def poll(self):
        return self._code

    def wait(self, timeout=None):
        self._code = 0
        return 0

    def kill(self):
        self._code = -9


def test_token_usage_fill_prefers_last_total():
    filled = cas._token_usage_fill(
        {
            "last": {"inputTokens": 12_000, "totalTokens": 12_500, "outputTokens": 500},
            "total": {"inputTokens": 99_000, "totalTokens": 100_000},
            "modelContextWindow": 272_000,
        }
    )
    assert filled["context_tokens"] == 12_500
    assert filled["model_context_window"] == 272_000


def test_compact_codex_thread_happy_path(monkeypatch):
    responses = [
        json.dumps({"id": 1, "result": {"userAgent": "codex/test"}}).encode() + b"\n",
        json.dumps(
            {"id": 2, "result": {"thread": {"id": "01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}
        ).encode()
        + b"\n",
        json.dumps({"id": 3, "result": {}}).encode() + b"\n",
        json.dumps(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": "01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "tokenUsage": {
                        "last": {"inputTokens": 8_000, "totalTokens": 8_200},
                        "modelContextWindow": 272_000,
                    },
                },
            }
        ).encode()
        + b"\n",
        json.dumps(
            {
                "method": "turn/completed",
                "params": {"turn": {"id": "t1", "status": "completed"}},
            }
        ).encode()
        + b"\n",
        json.dumps({"id": 4, "result": {"status": "unsubscribed"}}).encode() + b"\n",
    ]
    proc = _FakeProc(responses)

    def _popen(*_a, **_k):
        return proc

    monkeypatch.setattr(cas.subprocess, "Popen", _popen)
    monkeypatch.setattr(cas, "codex_executable", lambda: "codex.exe")

    # Drive reader synchronously: replace Thread so reader runs inline before loop.
    real_thread = threading.Thread

    def _inline_thread(target=None, args=(), daemon=None):
        class _T:
            def start(self_inner):
                if target is cas._reader:
                    target(*args)
                elif target is cas._drain_stderr:
                    target(*args)
                else:
                    real_thread(target=target, args=args, daemon=True).start()

        return _T()

    monkeypatch.setattr(cas.threading, "Thread", _inline_thread)

    result = cas.compact_codex_thread(
        "01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", cwd="C:/Projects/Cuttle", timeout=5.0
    )
    assert result["success"] is True
    assert result["method"] == "app-server-compact"
    assert result["token_usage"]["context_tokens"] == 8_200
    # initialize + initialized + resume + compact + unsubscribe
    joined = b"".join(proc.stdin.writes).decode("utf-8")
    assert "initialize" in joined
    assert "thread/resume" in joined
    assert "thread/compact/start" in joined


def test_compact_codex_thread_missing_bin(monkeypatch):
    monkeypatch.setattr(cas, "codex_executable", lambda: None)
    result = cas.compact_codex_thread("01aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert result["success"] is False
    assert "not found" in (result.get("error") or "").lower()
