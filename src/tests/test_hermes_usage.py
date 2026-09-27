"""Hermes session usage loading from state.db + per-turn deltas."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.utilities import hermes_cli_tool as hm


def test_usage_for_query_report_with_cache():
    u = hm.usage_for_query_report(
        "z-ai/glm-5.3-flash",
        {
            "input_tokens": 1000,
            "output_tokens": 50,
            "cache_read_tokens": 8000,
            "cost": 0.012,
        },
    )
    assert u["input_tokens"] == 1000
    assert u["output_tokens"] == 50
    assert u["cache_read_tokens"] == 8000
    assert u["cost"] == 0.012
    assert u["model"] == "z-ai/glm-5.3-flash"


def test_delta_hermes_usage_fresh_session_keeps_cost():
    after = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 50,
        "cost": 0.01,
        "model": "m",
    }
    out = hm._delta_hermes_usage({}, after)
    assert out["input_tokens"] == 100
    assert out["cache_read_tokens"] == 50
    assert out["cost"] == 0.01


def test_delta_hermes_usage_resume_subtracts_and_drops_cost():
    before = {
        "input_tokens": 1000,
        "output_tokens": 100,
        "cache_read_tokens": 5000,
        "cost": 0.5,
    }
    after = {
        "input_tokens": 1300,
        "output_tokens": 180,
        "cache_read_tokens": 9000,
        "cost": 0.7,
        "model": "m",
    }
    out = hm._delta_hermes_usage(before, after)
    assert out["input_tokens"] == 300
    assert out["output_tokens"] == 80
    assert out["cache_read_tokens"] == 4000
    assert "cost" not in out


def test_load_hermes_session_usage(tmp_path: Path, monkeypatch):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            reasoning_tokens INTEGER,
            estimated_cost_usd REAL,
            actual_cost_usd REAL
        )
        """
    )
    con.execute(
        """
        INSERT INTO sessions VALUES
        ('sess1', 'glm', 1000, 40, 800, 0, 0, 0.02, NULL)
        """
    )
    con.commit()
    con.close()
    monkeypatch.setattr(hm, "_hermes_home", lambda: tmp_path)
    u = hm.load_hermes_session_usage("sess1")
    assert u["input_tokens"] == 1000
    assert u["cache_read_tokens"] == 800
    assert u["cost"] == 0.02
    assert hm.load_hermes_session_usage("missing") == {}
