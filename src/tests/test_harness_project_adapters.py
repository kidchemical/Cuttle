"""Project-adapter trust boundary: opt-in gate, validate-before-import, load-once.

Project drop-ins (``{project}/.cuttle/agents/<id>/``) execute third-party
``adapter.py`` code. Required semantics:

1. Without explicit opt-in (``CUTTLE_ALLOW_PROJECT_ADAPTERS`` / settings),
   project ``adapter.py`` is never imported and the agent is not listed.
2. Manifest identity (folder id, ``slash``) is validated BEFORE the adapter
   module executes, so a malformed manifest cannot run code or hijack routing.
3. Each external adapter module executes once (import-time side effects do
   not re-run on every discovery/turn).
4. Sibling imports inside one drop-in do not leak into another drop-in
   (no cross-project shadowing via persistent ``sys.path``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import api.agent_harness.catalog as catalog
from api.agent_harness.catalog import (
    get_agent,
    list_agents,
    reload_catalog,
)


def _write_agent(
    agents_root: Path, agent_id: str, *, manifest_extra: str = "",
    adapter_src: str | None = None,
) -> Path:
    agent_dir = agents_root / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "manifest.yaml").write_text(
        f"label: Test {agent_id}\nslash: /{agent_id}\n{manifest_extra}",
        encoding="utf-8",
    )
    if adapter_src is None:
        adapter_src = (
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
        )
    (agent_dir / "adapter.py").write_text(adapter_src, encoding="utf-8")
    return agent_dir


@pytest.fixture
def project_agents(tmp_path, monkeypatch):
    """A temp ``{project}/.cuttle/agents`` root with opt-in enabled."""
    monkeypatch.setenv("CUTTLE_ALLOW_PROJECT_ADAPTERS", "1")
    proj = tmp_path / "proj"
    root = proj / ".cuttle" / "agents"
    root.mkdir(parents=True)
    reload_catalog()
    yield proj, root
    reload_catalog()


@pytest.fixture
def clean_import_state():
    """Snapshot/restore sys.path + sys.modules so drop-in loads stay hermetic."""
    saved_path = list(sys.path)
    saved_modules = set(sys.modules.keys())
    yield
    for key in [k for k in sys.modules if k not in saved_modules]:
        del sys.modules[key]
    sys.path[:] = saved_path


def test_unapproved_project_code_never_executes(
    tmp_path, monkeypatch, clean_import_state
):
    monkeypatch.delenv("CUTTLE_ALLOW_PROJECT_ADAPTERS", raising=False)
    proj = tmp_path / "proj"
    root = proj / ".cuttle" / "agents"
    root.mkdir(parents=True)
    touched = tmp_path / "pwned.txt"
    _write_agent(
        root,
        "sneaky",
        adapter_src=(
            f"open({str(touched)!r}, 'w').write('executed')\n"
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    reload_catalog()
    try:
        assert "sneaky" not in list_agents(str(proj))
        assert get_agent("sneaky", str(proj)) is None
        assert not touched.exists()
    finally:
        reload_catalog()


def test_opted_in_project_adapter_loads(
    project_agents, clean_import_state
):
    proj, root = project_agents
    _write_agent(root, "mytool")
    reload_catalog()
    pair = get_agent("mytool", str(proj))
    assert pair is not None
    manifest, _adapter = pair
    assert manifest.source == "project"
    assert "mytool" in list_agents(str(proj))


def test_external_adapter_module_executes_once(
    project_agents, clean_import_state, tmp_path
):
    proj, root = project_agents
    counter = tmp_path / "loads.txt"
    _write_agent(
        root,
        "counted",
        adapter_src=(
            f"open({str(counter)!r}, 'a').write('x')\n"
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    reload_catalog()
    assert get_agent("counted", str(proj)) is not None
    assert get_agent("counted", str(proj)) is not None
    assert get_agent("counted", str(proj)) is not None
    assert counter.read_text(encoding="utf-8") == "x"


def test_sibling_imports_do_not_cross_projects(
    project_agents, clean_import_state
):
    proj, root = project_agents
    for aid, marker in (("alpha", "AAA"), ("beta", "BBB")):
        d = _write_agent(
            root,
            aid,
            adapter_src=(
                "import helper\n"
                "MARKER = helper.MARKER\n"
                "class Adapter:\n"
                "    def available(self):\n"
                "        return True\n"
            ),
        )
        (d / "helper.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    reload_catalog()
    pair_a = get_agent("alpha", str(proj))
    pair_b = get_agent("beta", str(proj))
    assert pair_a is not None and pair_b is not None
    # Re-resolve the loaded adapter modules to read their bound MARKER.
    mods = {
        name: mod
        for name, mod in sys.modules.items()
        if name.startswith("cuttle_harness_ext_")
    }
    markers = sorted(getattr(m, "MARKER", None) for m in mods.values())
    assert markers == ["AAA", "BBB"]


def test_hijack_slash_rejected_before_import(
    project_agents, clean_import_state, tmp_path
):
    proj, root = project_agents
    touched = tmp_path / "hijack.txt"
    d = _write_agent(
        root,
        "hijack",
        manifest_extra='slash: "/"\n',
        adapter_src=(
            f"open({str(touched)!r}, 'w').write('executed')\n"
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    assert d.is_dir()
    reload_catalog()
    assert get_agent("hijack", str(proj)) is None
    assert "hijack" not in list_agents(str(proj))
    assert not touched.exists()


def test_noncanonical_folder_id_rejected_before_import(
    project_agents, clean_import_state, tmp_path
):
    proj, root = project_agents
    touched = tmp_path / "evil.txt"
    _write_agent(
        root,
        "Evil Agent",
        adapter_src=(
            f"open({str(touched)!r}, 'w').write('executed')\n"
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    reload_catalog()
    assert "Evil Agent" not in list_agents(str(proj))
    assert not touched.exists()


def test_underscore_folder_normalizes_to_canonical_id(
    project_agents, clean_import_state
):
    """``my_agent/`` behaves exactly like ``get_agent`` normalization (``my-agent``)."""
    proj, root = project_agents
    _write_agent(root, "my_agent")
    reload_catalog()
    pair = get_agent("my_agent", str(proj))
    assert pair is not None
    assert pair[0].id == "my-agent"


def test_unknown_capability_values_fall_back_to_defaults(
    project_agents, clean_import_state
):
    proj, root = project_agents
    _write_agent(root, "weird", manifest_extra='capabilities_inject: "sometimes"\n')
    reload_catalog()
    pair = get_agent("weird", str(proj))
    assert pair is not None
    assert pair[0].capabilities_inject == "sometimes"
