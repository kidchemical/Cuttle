"""Tests for context delta on resumed sessions."""

from __future__ import annotations

from tests.brain.context_support import ack_snapshot, delta_text


def test_compute_snapshot_tracks_rules(tmp_path):
    from api.cuttle_brain.context_delta import compute_snapshot

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    (rules / "01-a.md").write_text("Rule A", encoding="utf-8")
    snap = compute_snapshot(str(tmp_path))
    assert "01-a.md" in snap.project_rules
    assert snap.project_rules["01-a.md"]


def test_build_delta_on_rule_change(tmp_path):

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    (rules / "01-a.md").write_text("Rule A", encoding="utf-8")
    path = str(tmp_path)

    ack_snapshot("9009", "cursor", path)
    (rules / "01-a.md").write_text("Rule A updated with Discord pointer.", encoding="utf-8")

    resumed = delta_text("9009", "cursor", path)
    assert resumed is not None
    assert "<cuttle_context>" in resumed
    assert "Context delta" in resumed
    assert "Discord" in resumed


def test_build_resume_delta_none_when_unchanged(tmp_path):

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    (rules / "01-a.md").write_text("Stable", encoding="utf-8")
    path = str(tmp_path)

    ack_snapshot("9010", "cursor", path)
    assert delta_text("9010", "cursor", path) is None


def test_clear_injected_snapshot(tmp_path):
    from api.cuttle_brain.context_delta import clear_injected_snapshot

    rules = tmp_path / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    path = str(tmp_path)
    rule_file = rules / "01-a.md"
    rule_file.write_text("v1", encoding="utf-8")

    ack_snapshot("9011", "cursor", path)
    rule_file.write_text("v2", encoding="utf-8")
    assert delta_text("9011", "cursor", path) is not None

    clear_injected_snapshot("9011", "cursor", path)
    assert delta_text("9011", "cursor", path) is None
