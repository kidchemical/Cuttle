"""Global vs Cuttle-project config split (``.cuttle_global/`` vs ``.cuttle/``).

Global content compiles/resolves for every registered project. Cuttle-repo
content loads only when the chat targets Cuttle itself — guest projects must
never inherit Cuttle release/versioning mechanics.

Per-project ``GLOBAL.ini`` trims the global legs (shadow/off); the safety core
(``00-safety.md``) always survives. Personal markdown appends as a delta.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _guest(tmp_path: Path, router_ini: str | None = None) -> Path:
    guest = tmp_path / "guest"
    (guest / ".cuttle" / "rules").mkdir(parents=True)
    (guest / ".cuttle" / "rules" / "00-core.md").write_text("# Guest\n", encoding="utf-8")
    if router_ini is not None:
        (guest / ".cuttle" / "GLOBAL.ini").write_text(router_ini, encoding="utf-8")
    return guest


def test_global_rules_have_no_cuttle_release_content():
    from api.cuttle_brain.context_compiler import load_global_rules

    global_rules = load_global_rules()
    assert global_rules, "expected global rules under .cuttle_global/rules/"
    names = [n.lower() for n, _ in global_rules]
    assert "00-core.md" in names
    assert "00-safety.md" in names
    assert "04-versioning.md" not in names
    blob = " ".join(t for _, t in global_rules).lower()
    assert "electron/package.json" not in blob
    assert "bump-cuttle-version" not in blob
    assert "cuttle_package_version" not in blob


def test_guest_project_gets_global_rules_but_not_cuttle_versioning(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context

    guest = _guest(tmp_path)
    compiled = compile_context(
        "do guest work", project_path=str(guest), inject_capabilities=False
    )
    assert "global_rules" in compiled.layers_used
    assert "project_rules" in compiled.layers_used
    assert "bump-cuttle-version" not in compiled.prompt
    assert "04-versioning" not in compiled.prompt


def test_cuttle_repo_chat_gets_both_global_and_project_rules():
    from api.cuttle_brain.context_compiler import compile_context

    compiled = compile_context(
        "cuttle dev work",
        project_path=str(REPO_ROOT),
        inject_capabilities=False,
    )
    assert "global_rules" in compiled.layers_used
    assert "project_rules" in compiled.layers_used
    assert "Cuttle global rules" in compiled.prompt
    assert "Project rules" in compiled.prompt
    # Cuttle-only release discipline is present here (and only here).
    assert "cuttle-release.md" in compiled.prompt.lower()


def test_global_git_doc_has_no_release_section():
    body = (REPO_ROOT / ".cuttle_global" / "docs" / "git.md").read_text(encoding="utf-8")
    assert "Release versioning" not in body
    assert "bump-cuttle-version" not in body
    release = (REPO_ROOT / ".cuttle" / "docs" / "cuttle-release.md").read_text(encoding="utf-8")
    assert "bump-cuttle-version" in release


def test_global_action_resolves_from_guest_project(tmp_path):
    from api.project_actions import find_project_action_resolved

    guest = _guest(tmp_path)
    (guest / ".cuttle" / "actions").mkdir(parents=True)
    action, resolved_path = find_project_action_resolved(str(guest), "flask.restart")
    assert action is not None, "global flask.restart must resolve from any project"
    assert action["name"] == "flask.restart"
    assert Path(resolved_path).resolve() == REPO_ROOT.resolve()
    recipe = f"{action.get('run') or ''} {action.get('run_posix') or ''}"
    assert ".cuttle_global" in recipe.replace("\\", "/")


def test_global_scripts_compute_repo_root():
    """Moved global scripts must resolve the repo root through ``.cuttle_global/``."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "global_git_push", REPO_ROOT / ".cuttle_global" / "scripts" / "git-push.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert Path(mod.REPO_ROOT).resolve() == REPO_ROOT.resolve()


def test_safety_core_survives_rules_off(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context

    guest = _guest(tmp_path, "[global]\nrules = off\n")
    compiled = compile_context(
        "do guest work", project_path=str(guest), inject_capabilities=False
    )
    # Policy severed, safety core still compiled.
    assert "Cuttle global rules" in compiled.prompt
    assert "force-kill" in compiled.prompt
    assert "another project" in compiled.prompt
    assert "Cuttle global — always-on rules" not in compiled.prompt


def test_shadow_mode_replaces_same_basename_rule(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context, load_global_rules

    assert load_global_rules(), "global tree must exist for this test"
    guest = _guest(tmp_path, "[global]\nrules = shadow\n")
    (guest / ".cuttle" / "rules" / "01-chat-handles.md").write_text(
        "GUEST RETRACTION TOKEN", encoding="utf-8"
    )
    compiled = compile_context(
        "do guest work", project_path=str(guest), inject_capabilities=False
    )
    assert "GUEST RETRACTION TOKEN" in compiled.prompt
    # Global 01-chat-handles.md retracted for this project…
    assert "treat it as a Cuttle web-chat pointer" not in compiled.prompt
    # …but safety and other global policy still compile.
    assert "force-kill" in compiled.prompt
    assert "Cuttle global rules" in compiled.prompt


def test_docs_off_hides_global_inventory(tmp_path):
    from api.cuttle_brain.context_compiler import compile_context

    guest = _guest(tmp_path, "[global]\ndocs = off\n")
    compiled = compile_context(
        "do guest work", project_path=str(guest), inject_capabilities=False
    )
    assert "global docs: off per this project's GLOBAL.ini" in compiled.prompt
    assert "global docs (Cuttle)" not in compiled.prompt


def test_actions_off_blocks_global_fallback(tmp_path):
    from api.project_actions import find_project_action_resolved

    guest = _guest(tmp_path, "[global]\nactions = off\n")
    (guest / ".cuttle" / "actions").mkdir(parents=True)
    action, _ = find_project_action_resolved(str(guest), "flask.restart")
    assert action is None, "severed global leg must not resolve hub actions"


def test_malformed_router_falls_back_to_defaults(tmp_path):
    from api.cuttle_brain.global_layers import load_global_layers

    guest = _guest(tmp_path, "[global\nrules = off\n")
    cfg = load_global_layers(str(guest))
    assert cfg.rules_mode == "append"
    assert cfg.docs is True
    assert cfg.actions is True
    guest2 = _guest(tmp_path / "x")
    (guest2 / ".cuttle" / "GLOBAL.ini").write_text(
        "[global]\nrules = vaporize\ndocs = maybe\nbogus = 1\n", encoding="utf-8"
    )
    cfg2 = load_global_layers(str(guest2))
    assert cfg2.rules_mode == "append"
    assert cfg2.docs is True
