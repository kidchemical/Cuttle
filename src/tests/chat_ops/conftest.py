"""Supervision checks never read this machine's process table, memory or display."""

import pytest


@pytest.fixture(autouse=True)
def _no_host_readings(monkeypatch):
    from api.chat_ops import display_care, system_care

    monkeypatch.setattr(system_care, "_work_tree", lambda: {"owners": []})
    monkeypatch.setattr(system_care, "_memory", lambda: None)
    monkeypatch.setattr(display_care, "_display", lambda: None)
    monkeypatch.setattr(display_care, "_Hold", lambda: pytest.fail("took a real display inhibitor"))
