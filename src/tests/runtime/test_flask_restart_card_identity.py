"""Restart cards: one standard chooser, and ids that never repeat.

Two long-running failures this pins:

* Agents hand-wrote trimmed/retitled restart cards. Any card that is a Flask
  restart (title or a ``flask.restart`` option) must render as the single
  standard chooser, including the bare stub quoted in ``00-safety.md``.
* The daemon's generation counter restarted at 1 on every cold boot, so a new
  ``flask-restart-g1`` card collided with an old, already-clicked one and
  rendered as used (no progress bar). The counter is now persisted.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from api.action_forms import normalize_action_form_spec
from core.flask_generation import LEGACY_FLOOR, next_generation

REPO = Path(__file__).resolve().parents[3]
STANDARD_IDS = ["status", "graceful", "when-idle", "force", "health"]


def _modes(spec):
    return [("health" if o["action"] == "flask.health" else o["params"]["mode"]) for o in spec["options"]]


def _assert_standard(spec):
    assert spec["title"] == "Restart Flask (daemon-owned)"
    assert _modes(spec) == STANDARD_IDS
    assert spec["silent"] is True and spec["resume"] is False


def test_safety_rule_stub_expands_to_the_standard_chooser():
    rule = (REPO / ".cuttle_global" / "rules" / "00-safety.md").read_text(encoding="utf-8")
    stub = re.search(r"`<cuttle_action_form>(.*?)</cuttle_action_form>`", rule, re.S)
    assert stub, "00-safety.md must quote the restart card stub"
    _assert_standard(normalize_action_form_spec(json.loads(stub.group(1))))


def test_hand_written_restart_cards_are_canonicalized():
    trimmed = {  # what agents kept emitting: retitled, three options
        "title": "Restart Flask to load avatar uploads", "mode": "choice",
        "options": [
            {"id": "when-idle", "label": "When idle", "action": "flask.restart", "params": {"mode": "when-idle"}},
            {"id": "graceful", "label": "Graceful", "action": "flask.restart", "params": {"mode": "graceful"}},
            {"id": "status", "label": "Status", "action": "flask.restart", "params": {"mode": "status"}},
        ],
    }
    _assert_standard(normalize_action_form_spec(trimmed))
    _assert_standard(normalize_action_form_spec({"options": [{"action": "flask.restart"}]}))
    _assert_standard(normalize_action_form_spec({"title": "Flask restart"}))


def test_non_restart_empty_cards_are_still_dropped():
    assert normalize_action_form_spec({"title": "Pick one"}) is None


def test_generation_jumps_clear_of_legacy_ids_then_increases(tmp_path):
    path = tmp_path / "gen"
    first = next_generation(path, 0)
    assert first == LEGACY_FLOOR + 1
    assert next_generation(path, first) == first + 1
    # Cold boot: the daemon's in-memory counter is back to 0, the file is not.
    assert next_generation(path, 0) == first + 2
    assert path.read_text() == str(first + 2)


def test_generation_respects_seen_values_and_survives_write_failure(tmp_path):
    path = tmp_path / "gen"
    path.write_text("5")
    assert next_generation(path, 0, seen=[9]) == 10
    blocked = tmp_path / "file-not-dir"
    blocked.write_text("x")
    assert next_generation(blocked / "gen", 3) == LEGACY_FLOOR + 1


def test_daemon_hands_out_persisted_generations(tmp_path, monkeypatch):
    daemon = REPO / "src" / "scripts" / "cuttle_daemon.py"
    spec = importlib.util.spec_from_file_location("cuttle_daemon_generation_test", daemon)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr("core.runtime_paths.flask_generation_path", lambda *a, **k: tmp_path / "gen")
    monkeypatch.setattr(mod, "FLASK_RESTART_STATUS_PATH", tmp_path / "status.json")
    (tmp_path / "status.json").write_text(json.dumps({"generation": 2}))
    a = mod._next_flask_generation(0)
    b = mod._next_flask_generation(0)  # simulated cold boot
    assert a == LEGACY_FLOOR + 1 and b == a + 1
    source = daemon.read_text(encoding="utf-8")
    assert "_flask_generation += 1" not in source
