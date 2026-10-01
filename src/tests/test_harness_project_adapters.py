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
                "from . import helper\n"
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
    # Read each drop-in's own namespaced `.helper` submodule: same basename,
    # per-adapter values, no bare `helper` residue.
    helpers = {
        name: mod
        for name, mod in sys.modules.items()
        if name.startswith("cuttle_harness_ext_") and name.endswith(".helper")
    }
    assert sorted(m.MARKER for m in helpers.values()) == ["AAA", "BBB"]
    assert "helper" not in sys.modules


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


# --- P6-B follow-up: namespaced package loading, no sys.path mutation ---

_PKG_ADAPTER = (
    "from . import helper\n"
    "from .sub.deep import VAL\n"
    "COMBINED = (helper.MARKER, VAL)\n"
    "class Adapter:\n"
    "    def __init__(self):\n"
    "        self.combined = COMBINED\n"
    "    def available(self):\n"
    "        return True\n"
    "def build_adapter():\n"
    "    return Adapter()\n"
)


def _write_pkg_agent(agents_root: Path, agent_id: str, marker: str, deep: str):
    d = agents_root / agent_id
    (d / "sub").mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(
        f"label: Test {agent_id}\nslash: /{agent_id}\n", encoding="utf-8"
    )
    (d / "adapter.py").write_text(_PKG_ADAPTER, encoding="utf-8")
    (d / "helper.py").write_text(f"MARKER = {marker!r}\n", encoding="utf-8")
    (d / "sub" / "deep.py").write_text(f"VAL = {deep!r}\n", encoding="utf-8")
    return d


def test_dotted_relative_siblings_are_namespaced_per_adapter(
    project_agents, clean_import_state
):
    """`from . import helper` + `from .sub.deep import VAL` resolve per drop-in.

    No `sys.path` entry, no bare `helper` residue: same basenames in two
    drop-ins (including a dotted subpackage) must not leak into each other.
    """
    import sys as _sys

    proj, root = project_agents
    path_before = list(_sys.path)
    _write_pkg_agent(root, "pkgone", "P1", "D1")
    _write_pkg_agent(root, "pkgtwo", "P2", "D2")
    reload_catalog()
    one = get_agent("pkgone", str(proj))
    two = get_agent("pkgtwo", str(proj))
    assert one is not None and two is not None
    assert one[1].combined == ("P1", "D1")
    assert two[1].combined == ("P2", "D2")
    assert "helper" not in _sys.modules
    assert list(_sys.path) == path_before


def test_preexisting_unrelated_module_is_never_overwritten(
    project_agents, clean_import_state, monkeypatch
):
    """A live `helper` module belonging to someone else survives the load."""
    import sys as _sys
    import types as _types

    fake = _types.ModuleType("helper")
    fake.SENTINEL = "UNRELATED"
    monkeypatch.setitem(_sys.modules, "helper", fake)
    proj, root = project_agents
    _write_pkg_agent(root, "relonly", "MINE", "DD")
    reload_catalog()
    pair = get_agent("relonly", str(proj))
    assert pair is not None
    assert pair[1].combined == ("MINE", "DD")
    assert _sys.modules["helper"] is fake
    assert _sys.modules["helper"].SENTINEL == "UNRELATED"


def test_relative_sibling_top_level_binding(
    project_agents, clean_import_state
):
    """Canonical `from . import helper` binds at top level, leaves no trace."""
    import sys as _sys

    proj, root = project_agents
    path_before = list(_sys.path)
    _write_agent(
        root,
        "relbind",
        adapter_src=(
            "from . import helper\n"
            "MARKER = helper.MARKER\n"
            "class Adapter:\n"
            "    def __init__(self):\n"
            "        self.marker = MARKER\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    (root / "relbind" / "helper.py").write_text("MARKER = 'REL'\n", encoding="utf-8")
    reload_catalog()
    pair = get_agent("relbind", str(proj))
    assert pair is not None
    assert pair[1].marker == "REL"
    assert "helper" not in _sys.modules
    assert list(_sys.path) == path_before


def test_factory_time_relative_import_resolves(
    project_agents, clean_import_state
):
    """`from . import helper` inside `build_adapter()` resolves via package."""
    import sys as _sys

    proj, root = project_agents
    _write_agent(
        root,
        "factrel",
        adapter_src=(
            "class Adapter:\n"
            "    def __init__(self, marker):\n"
            "        self.marker = marker\n"
            "    def available(self):\n"
            "        return True\n"
            "def build_adapter():\n"
            "    from . import helper\n"
            "    return Adapter(helper.MARKER)\n"
        ),
    )
    (root / "factrel" / "helper.py").write_text("MARKER = 'FAC'\n", encoding="utf-8")
    reload_catalog()
    pair = get_agent("factrel", str(proj))
    assert pair is not None
    assert pair[1].marker == "FAC"
    assert "helper" not in _sys.modules


def test_stdlib_name_is_never_shadowed(project_agents, clean_import_state):
    """A drop-in `email.py` must not shadow stdlib, even mid-load."""
    import sys as _sys

    _sys.modules.pop("email", None)
    proj, root = project_agents
    _write_agent(
        root,
        "stdemail",
        adapter_src=(
            "import email\n"
            "IS_STDLIB = hasattr(email, 'message_from_string')\n"
            "class Adapter:\n"
            "    def __init__(self):\n"
            "        self.is_stdlib = IS_STDLIB\n"
            "    def available(self):\n"
            "        return True\n"
            "def build_adapter():\n"
            "    return Adapter()\n"
        ),
    )
    (root / "stdemail" / "email.py").write_text("MARKER = 'EVIL'\n", encoding="utf-8")
    reload_catalog()
    pair = get_agent("stdemail", str(proj))
    assert pair is not None
    assert pair[1].is_stdlib is True


def test_concurrent_same_adapter_executes_once(project_agents, clean_import_state):
    """Barrier-synchronized discovery from many threads: one exec, one entry."""
    import sys as _sys
    import threading

    proj, root = project_agents
    counter = root / "counter.txt"
    counter.write_text("", encoding="utf-8")
    _write_agent(
        root,
        "racy",
        adapter_src=(
            f"open({str(counter)!r}, 'a').write('x')\n"
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    reload_catalog()
    barrier = threading.Barrier(8)
    results = []

    def worker():
        barrier.wait(timeout=30)
        results.append(get_agent("racy", str(proj)) is not None)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(results) and len(results) == 8
    assert counter.read_text(encoding="utf-8") == "x"


def test_concurrent_different_adapters_stay_correct(
    project_agents, clean_import_state
):
    """Simultaneous loads of two same-sibling-name adapters stay isolated."""
    import threading

    proj, root = project_agents
    _write_pkg_agent(root, "concone", "C1", "E1")
    _write_pkg_agent(root, "conctwo", "C2", "E2")
    reload_catalog()
    barrier = threading.Barrier(8)
    results = []

    def worker(i):
        barrier.wait(timeout=30)
        aid = "concone" if i % 2 == 0 else "conctwo"
        pair = get_agent(aid, str(proj))
        results.append((aid, pair[1].combined if pair else None))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(results) == 8
    for aid, combined in results:
        assert combined == ("C1", "E1") if aid == "concone" else ("C2", "E2")


def test_failed_load_cleans_import_state_and_stays_quiet(
    project_agents, clean_import_state, tmp_path
):
    """A raising adapter leaves no modules behind and is not re-executed."""
    import sys as _sys

    proj, root = project_agents
    counter = tmp_path / "boom.txt"
    counter.write_text("", encoding="utf-8")
    d = _write_agent(
        root,
        "boom",
        adapter_src=(
            f"open({str(counter)!r}, 'a').write('x')\n"
            "raise RuntimeError('boom')\n"
        ),
    )
    (d / "boom_helper.py").write_text("MARKER = 'B'\n", encoding="utf-8")
    path_before = list(_sys.path)
    reload_catalog()
    assert get_agent("boom", str(proj)) is None
    assert get_agent("boom", str(proj)) is None
    assert counter.read_text(encoding="utf-8") == "x"
    assert "boom_helper" not in _sys.modules
    assert not [k for k in _sys.modules if k.startswith("cuttle_harness_ext_boom")]
    assert list(_sys.path) == path_before


def test_unused_sibling_never_executes(project_agents, clean_import_state, tmp_path):
    """Discovery must not run utility scripts the adapter never imports."""
    import sys as _sys

    proj, root = project_agents
    sentinel = tmp_path / "unused_ran.txt"
    d = _write_agent(root, "restrained")
    (d / "unused.py").write_text(
        f"open({str(sentinel)!r}, 'w').write('ran')\nraise RuntimeError('unused')\n",
        encoding="utf-8",
    )
    path_before = list(_sys.path)
    reload_catalog()
    pair = get_agent("restrained", str(proj))
    assert pair is not None
    assert not sentinel.exists()
    assert "unused" not in _sys.modules
    assert list(_sys.path) == path_before


def test_broken_transitive_sibling_fails_only_when_imported(
    project_agents, clean_import_state
):
    """A raising dependency surfaces at the importer; unrelated files ignored."""
    import sys as _sys

    proj, root = project_agents
    d = _write_agent(
        root,
        "needschain",
        adapter_src=(
            "from . import mid\n"
            "class Adapter:\n"
            "    def __init__(self):\n"
            "        self.value = mid.VALUE\n"
            "    def available(self):\n"
            "        return True\n"
            "def build_adapter():\n"
            "    return Adapter()\n"
        ),
    )
    (d / "mid.py").write_text(
        "from . import leaf\nVALUE = 'chain:' + leaf.VALUE\n", encoding="utf-8"
    )
    (d / "leaf.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    (d / "bystander.py").write_text("raise RuntimeError('bystander')\n", encoding="utf-8")
    reload_catalog()
    pair = get_agent("needschain", str(proj))
    assert pair is not None
    assert pair[1].value == "chain:ok"
    assert "bystander" not in _sys.modules


def test_imported_broken_sibling_cleans_up(project_agents, clean_import_state):
    """`from . import broken` (raising) skips the adapter with no residue."""
    import sys as _sys

    proj, root = project_agents
    d = _write_agent(
        root,
        "needsbroken",
        adapter_src="from . import broken\n",
    )
    (d / "broken.py").write_text("raise RuntimeError('broken dep')\n", encoding="utf-8")
    path_before = list(_sys.path)
    reload_catalog()
    assert get_agent("needsbroken", str(proj)) is None
    assert "broken" not in _sys.modules
    assert not [k for k in _sys.modules if "needsbroken" in k]
    assert list(_sys.path) == path_before


def test_legacy_absolute_sibling_is_rejected(project_agents, clean_import_state):
    """Declared contract: bare `import helper` is NOT a sibling import.

    The adapter is skipped (never half-loads), nothing is aliased, and the
    failure names the missing module so the author migrates to
    `from . import helper`.
    """
    import sys as _sys

    _sys.modules.pop("helper", None)
    proj, root = project_agents
    d = _write_agent(
        root,
        "legone",
        adapter_src=(
            "import helper\n"
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    (d / "helper.py").write_text("MARKER = 'LEG'\n", encoding="utf-8")
    path_before = list(_sys.path)
    reload_catalog()
    assert get_agent("legone", str(proj)) is None
    assert "helper" not in _sys.modules
    assert not [k for k in _sys.modules if "legone" in k]
    assert list(_sys.path) == path_before


def test_relative_lazy_import_works_after_load(project_agents, clean_import_state):
    """Canonical relative imports are not confined to a load window: the
    package persists, so a method called long after discovery resolves."""
    proj, root = project_agents
    d = _write_agent(
        root,
        "lazrel",
        adapter_src=(
            "class Adapter:\n"
            "    def available(self):\n"
            "        return True\n"
            "    def describe(self):\n"
            "        from . import helper\n"
            "        return helper.MARKER\n"
            "def build_adapter():\n"
            "    return Adapter()\n"
        ),
    )
    (d / "helper.py").write_text("MARKER = 'LAZY'\n", encoding="utf-8")
    reload_catalog()
    pair = get_agent("lazrel", str(proj))
    assert pair is not None
    assert pair[1].describe() == "LAZY"


def test_edited_adapter_reloads_on_next_discovery(
    project_agents, clean_import_state
):
    """mtime-keyed cache: editing adapter.py takes effect without restart."""
    proj, root = project_agents
    d = _write_agent(
        root,
        "hotedit",
        adapter_src=(
            "MARKER = 'V1'\n"
            "class Adapter:\n"
            "    def __init__(self):\n"
            "        self.marker = MARKER\n"
            "    def available(self):\n"
            "        return True\n"
        ),
    )
    reload_catalog()
    assert get_agent("hotedit", str(proj))[1].marker == "V1"
    (d / "adapter.py").write_text(
        (d / "adapter.py").read_text(encoding="utf-8").replace("V1", "V2"),
        encoding="utf-8",
    )
    assert get_agent("hotedit", str(proj))[1].marker == "V2"
