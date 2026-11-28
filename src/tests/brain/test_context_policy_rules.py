"""Offline regression suite: context policy scoping for shipped guidance.

Compiles matched isolated Cuttle vs synthetic-guest contexts from COPIES of
the tracked shipped rule files (never the checkout's live overlay, except in
the one explicit overlay case). No vendors, no ranking judge. These tests
guard which guidance reaches which project — they do NOT prove an LLM
follows instructions, and character counts are dump guardrails, not billing
claims.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

import api.cuttle_brain.context_compiler as _compiler
from core.runtime_paths import personal_dir
from api.cuttle_brain.context_compiler import compile_context

REPO_ROOT = Path(__file__).resolve().parents[3]

# Generous dump guardrail for the global always-on body (current ~10.1K;
# the old ~12.3K payload fails). Catches a future full-runbook paste;
# never freeze an exact length.
# Ceilings, not targets, measured with paths replaced (`_measured`) on
# 2026-11-28 at 7,438 (global rule bodies), 14,684 (Cuttle-repo envelope)
# and 11,860 (guest), about 3% headroom each. That set came after the
# desktop-control rule and skill and the busy-machine pointer were trimmed.
# Raising one means always-on text grew. The change that crosses a ceiling
# trims first, starting with its own text, then moves procedure to its
# runbook (`rule-authoring.md`). The assertion message gives each section's
# size.
GLOBAL_RULE_BODY_BUDGET = 7700
CUTTLE_ENVELOPE_BUDGET = 15100
GUEST_ENVELOPE_BUDGET = 12200

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
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
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
    assert "Do not use native\n   harness question or input tools" not in env
    # Shadow with a project safety twin: twin appends, base survives.
    twin = "# Guest safety\n\nGuest confirm marker.\n"
    env = _env("hello", _guest(
        tmp_path, global_rules_mode="shadow", safety_twin=twin))
    assert "taskkill" in env
    assert "Guest confirm marker" in env


def test_personal_overlay_appends_explicitly(
        tmp_path, _no_providers_and_isolated_install):
    personal = personal_dir()
    (personal / "rules").mkdir(parents=True, exist_ok=True)
    (personal / "rules" / "00-core.md").write_text(
        "# Local\n\nOverlay marker.\n", encoding="utf-8")
    env = _env("hello", _guest(tmp_path))
    assert "Overlay marker" in env


def test_personal_only_rule_is_labelled_install_local(
        tmp_path, _no_providers_and_isolated_install):
    """A personal rule with no tracked twin must not read as shipped guidance:
    an agent quoting it has to be able to say where it came from."""
    personal = personal_dir()
    (personal / "rules").mkdir(parents=True, exist_ok=True)
    (personal / "rules" / "10-local-only.md").write_text(
        "# Local\n\nLocal-only marker.\n", encoding="utf-8")
    env = _env("hello", _guest(tmp_path))
    head, _, body = env.partition("Local-only marker")
    assert body and "Install-local (`personal/rules/10-local-only.md`" in head[-200:]


def test_shipped_always_on_text_names_no_retired_process(tmp_path):
    """Discord is REST-only agent-ops: there is no bot process to protect, and a
    kill prohibition naming one sends agents hunting for it."""
    from api.cuttle_ui_capabilities import cuttle_ui_capabilities_block
    from api.restart_safety_policy import RESTART_SAFETY_BRIEF

    shipped = _env("hello", _guest(tmp_path)) + cuttle_ui_capabilities_block() + RESTART_SAFETY_BRIEF
    assert "discord bot" not in shipped.lower()
    assert "cuttle_daemon" in shipped and "web_chat_api" in shipped


def test_long_project_doc_list_becomes_a_count(tmp_path):
    few, many = _guest(tmp_path), _guest(tmp_path)
    for proj, n in ((few, 3), (many, 20)):
        docs = Path(proj) / ".cuttle" / "docs"
        docs.mkdir()
        for i in range(n):
            (docs / f"note-{i:02d}.md").write_text("# Note\n", encoding="utf-8")
    assert "- docs: note-00.md, note-01.md, note-02.md" in _env("hello", few)
    env = _env("hello", many)
    assert f"- docs: 20 runbooks and notes under `{Path(many).resolve() / '.cuttle' / 'docs'}`" in env
    assert "note-07.md" not in env


def test_global_rule_body_budget_with_counts(tmp_path, capsys):
    bodies = dict(_compiler.load_global_rules())
    total = sum(len(text) for text in bodies.values())
    env = _env("hello", _cuttle_project(tmp_path))
    print(f"global_rule_bodies={total} "
          f"files={len(bodies)} envelope={len(env)}")
    assert total < GLOBAL_RULE_BODY_BUDGET


def _measured(envelope, project, tmp_path):
    """Envelope text with checkout and temp paths replaced by short placeholders.

    The briefing names the project root, the docs folder and each skill's
    SKILL.md, so raw length depends on where the tree is checked out.
    """
    for root, mark in ((Path(project), "<project>"), (tmp_path, "<tmp>"), (REPO_ROOT, "<repo>")):
        for form in {str(root), root.as_posix()}:
            envelope = envelope.replace(form, mark)
    return envelope


def _sections(envelope):
    """Size of each briefing section, so an overflow names the part that grew."""
    sizes, name, start = {}, "preamble", 0
    heads = r"^(<cuttle_ui_capabilities>|### \S+\.md|## (?:Project rules|Profile|Runtime context).*)$"
    for match in re.finditer(heads, envelope, re.M):
        sizes[name] = sizes.get(name, 0) + match.start() - start
        name, start = match.group(1).strip("<>#: "), match.start()
    sizes[name] = sizes.get(name, 0) + len(envelope) - start
    return ", ".join(f"{key}={size}" for key, size in sizes.items())


def test_fresh_envelope_stays_inside_its_budget(tmp_path, capsys):
    """A fresh `hello` briefing (tracked rules, no personal overlay, no ranker)."""
    cuttle_root, guest_root = _cuttle_project(tmp_path), _guest(tmp_path)
    cuttle = _measured(_env("hello", cuttle_root), cuttle_root, tmp_path)
    guest = _measured(_env("hello", guest_root), guest_root, tmp_path)
    print(f"cuttle_envelope={len(cuttle)} guest_envelope={len(guest)}")
    assert len(cuttle) < CUTTLE_ENVELOPE_BUDGET, _sections(cuttle)
    assert len(guest) < GUEST_ENVELOPE_BUDGET, _sections(guest)


def test_envelope_measure_ignores_the_checkout_path(tmp_path):
    short, deep = tmp_path / "a", tmp_path / ("very-long-checkout-folder-" * 4)
    roots = [_guest(base) for base in (short, deep)]
    sizes = {len(_measured(_env("hello", root), root, tmp_path)) for root in roots}
    assert len(sizes) == 1


@pytest.mark.parametrize("prompt", ["hello", "Fix the native input integration"])
@pytest.mark.parametrize("project", ["cuttle", "guest"])
def test_question_rule_is_harness_neutral(prompt, project, tmp_path):
    path = _cuttle_project(tmp_path) if project == "cuttle" else _guest(tmp_path)
    env = _env(prompt, path)
    assert 'Do not use native\n   harness question or input tools' in env
    core = dict(_compiler.load_global_rules())["00-core.md"]
    assert "AskQuestion" not in core and "request_user_input" not in core
    assert '<cuttle_action_form>' in core and '"resume": true' in core
