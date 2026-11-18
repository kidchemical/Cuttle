"""Supervision checks never read this machine's process table or memory."""

import pytest


@pytest.fixture(autouse=True)
def _no_host_readings(monkeypatch):
    from api.chat_ops import system_care

    monkeypatch.setattr(system_care, "_work_tree", lambda: {"owners": []})
    monkeypatch.setattr(system_care, "_memory", lambda: None)
