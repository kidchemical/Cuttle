"""Offline regression suite: context policy scoping for shipped guidance.

Compiles matched isolated Cuttle vs synthetic-guest contexts from COPIES of
the tracked shipped rule files (never the checkout's live overlay, except in
the one explicit overlay case). No vendors, no ranking judge. These tests
guard which guidance reaches which project — they do NOT prove an LLM
follows instructions, and character counts are dump guardrails, not billing
claims.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import api.cuttle_brain.context_compiler as _compiler
from api.cuttle_brain.context_compiler import compile_context

REPO_ROOT = Path(__file__).resolve().parents[2]

# Generous dump guardrail for the global always-on body (current ~10.1K;
# the old ~12.3K payload fails). Catches a future full-runbook paste;
# never freeze an exact length.
GLOBAL_RULE_BODY_BUDGET = 12000

CUTTLE_POINTERS = (
    "ARCHITECTURE_PRINCIPLES.md",
    "repository-map.md",
    "development-instance-safety.md",
)

# Exact launch recipe from the Shadow runbook section of
# development-instance-safety.md: real procedure text that must only ever
# be read on demand, never compiled always-on.
PROCEDURE_RECIPE = "api.dev_instance up"


@pytest.fixture(autouse=True)
def _no_providers_and_isolated_install(tmp_path, monkeypatch):
    """Ordinary pytest never makes an unintended provider call.

    Disables the optional judge in-process (belt over the shell recipe) and
    redirects the install root at copies of the shipped global files, so the
    checkout's live personal overlay cannot leak into base measurements.
    """
    monkeypatch.setenv("CUTTLE_JEV_DISABLED", "1")
    for key in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY",
                "CUTTLE_JEV_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    iso = tmp_path / "install"
    shutil.copytree(REPO_ROOT / ".cuttle_global" / "rules",
                    iso / ".cuttle_global" / "rules")
    shutil.copytree(REPO_ROOT / ".cuttle_global" / "docs",
                    iso / ".cuttle_global" / "docs")
    monkeypatch.setattr(_compiler, "_cuttle_install_root",
                        lambda: iso)
    return iso


def _cuttle_project(tmp_path):
    """Isolated Cuttle project: copies of the shipped project rule files."""
    proj = tmp_path / "cuttle"
    rules = proj / ".cuttle" / "rules"
    rules.mkdir(parents=True)
    for src in (REPO_ROOT / ".cuttle" / "rules").glob("*.md"):
        shutil.copy(src, rules / src.name)
    return str(proj)


_GUEST_N = 0


def _guest(tmp_path, global_rules_mode=None, safety_twin=None):
    global _GUEST_N
    _GUEST_N += 1
    proj = tmp_path / f"guest-{_GUEST_N}"
    (proj / ".cuttle" / "rules").mkdir(parents=True)
    (proj / ".cuttle" / "rules" / "00-x.md").write_text(
        "# Guest\n\nGuest rule.\n", encoding="utf-8")
    if safety_twin is not None:
        (proj / ".cuttle" / "rules" / "00-safety.md").write_text(
            safety_twin, encoding="utf-8")
    if global_rules_mode:
        (proj / ".cuttle" / "GLOBAL.ini").write_text(
            f"[global]\nrules = {global_rules_mode}\n", encoding="utf-8")
    return str(proj)


def _env(prompt, project_path):
    return compile_context(prompt, project_path=project_path).envelope


def test_cuttle_coding_gets_architecture_pointers(tmp_path):
    env = _env("fix the router bug", _cuttle_project(tmp_path))
    for marker in CUTTLE_POINTERS:
        assert marker in env, marker


def test_guest_gets_no_cuttle_pointers(tmp_path):
    env = _env("write a python function", _guest(tmp_path))
    for marker in ("repository-map", "ARCHITECTURE",
                   "development-instance-safety"):
        assert marker not in env, marker


def test_launch_recipe_absent_pointer_only(tmp_path):
    # Cuttle hello, Cuttle bug, guest hello: the agent gets the pointer
    # telling it to read the safety doc, never the launch recipe itself.
    cuttle = _cuttle_project(tmp_path)
    for prompt, proj in (("hello", cuttle),
                         ("fix the login crash", cuttle),
                         ("hello", _guest(tmp_path))):
        assert PROCEDURE_RECIPE not in _env(prompt, proj), (prompt, proj)
    assert "development-instance-safety.md" in _env(
        "fix the login crash", cuttle)


def test_mandatory_safety_present_for_guest(tmp_path):
    env = _env("hello", _guest(tmp_path))
    assert "taskkill" in env  # host-process safety
    assert "cuttle_confirm" in env  # cross-project write confirm


def test_safety_survives_policy_off_and_shadow_twin(tmp_path):
    # GLOBAL.ini off: both safety invariants survive, 00-core drops out.
    env = _env("hello", _guest(tmp_path, global_rules_mode="off"))
    assert "taskkill" in env
    assert "cuttle_confirm" in env
    assert "AskQuestion" not in env
    # Shadow with a project safety twin: twin appends, base survives.
    twin = "# Guest safety\n\nGuest confirm marker.\n"
    env = _env("hello", _guest(
        tmp_path, global_rules_mode="shadow", safety_twin=twin))
    assert "taskkill" in env
    assert "Guest confirm marker" in env


def test_personal_overlay_appends_explicitly(
        tmp_path, _no_providers_and_isolated_install):
    iso = _no_providers_and_isolated_install
    (iso / ".cuttle_global" / "personal" / "rules").mkdir(parents=True)
    (iso / ".cuttle_global" / "personal" / "rules" / "00-core.md").write_text(
        "# Local\n\nOverlay marker.\n", encoding="utf-8")
    env = _env("hello", _guest(tmp_path))
    assert "Overlay marker" in env


def test_global_rule_body_budget_with_counts(tmp_path, capsys):
    bodies = dict(_compiler.load_global_rules())
    total = sum(len(text) for text in bodies.values())
    env = _env("hello", _cuttle_project(tmp_path))
    print(f"global_rule_bodies={total} "
          f"files={len(bodies)} envelope={len(env)}")
    assert total < GLOBAL_RULE_BODY_BUDGET
