"""Subsystem grouping, community labels, and the curated Obsidian wiki (pure)."""

from __future__ import annotations

import re

from api.knowledge_graph import wiki
from api.knowledge_graph.subsystems import build_view, community_labels, subsystem_keys


def _node(nid, label, path, *, community=0, ftype="code", loc="L10"):
    return {"id": nid, "label": label, "source_file": path, "file_type": ftype,
            "source_location": loc, "community": community}


def _graph():
    nodes = [
        _node("r_init", "Router package — pick a harness.", "src/api/router/__init__.py",
              ftype="rationale", loc="L1"),
        _node("r_file", "router/engine.py", "src/api/router/engine.py", loc="L1"),
        _node("r_decide", "decide()", "src/api/router/engine.py"),
        _node("r_types", "Decision", "src/api/router/types.py"),
        _node("r_cfg", "load_config()", "src/api/router/config.py"),
        _node("h_run", "run_agent()", "src/api/harness/run.py", community=1),
        _node("h_kernel", "Kernel", "src/api/harness/kernel.py", community=1),
        _node("h_types", "AgentResult", "src/api/harness/types.py", community=1),
        _node("ext", "pathlib", "", community=2, ftype="code", loc=""),
    ]
    links = [
        {"source": "r_decide", "target": "h_run", "relation": "calls"},
        {"source": "r_decide", "target": "r_types", "relation": "references"},
        {"source": "r_cfg", "target": "h_types", "relation": "imports_from"},
        {"source": "h_run", "target": "h_kernel", "relation": "calls"},
        {"source": "r_decide", "target": "ext", "relation": "imports"},
        {"source": "h_run", "target": "ext", "relation": "imports"},
        {"source": "h_kernel", "target": "ext", "relation": "imports"},
        {"source": "r_init", "target": "r_file", "relation": "rationale_for"},
    ]
    return {"nodes": nodes, "links": links}


def test_small_dirs_roll_up_and_crowded_flat_dirs_split_by_prefix():
    files = [f"src/api/chat_{i}.py" for i in range(5)] + \
            [f"src/api/mod{i}.py" for i in range(20)] + \
            ["src/api/agents/codex/adapter.py", "src/api/agents/codex/runner.py",
             "src/api/agents/codex/types.py", "src/api/agents/odd/one.py"]
    keys = subsystem_keys(files)
    assert keys["src/api/chat_0.py"] == "src/api/chat_*"
    assert keys["src/api/mod3.py"] == "src/api"
    assert keys["src/api/agents/codex/runner.py"] == "src/api/agents/codex"
    # a one-file dir never becomes its own subsystem
    assert keys["src/api/agents/odd/one.py"] != "src/api/agents/odd"


def test_view_aggregates_dependencies_and_package_summary():
    view = build_view(_graph(), "Demo")
    router, harness = view.subsystems["api › router"], view.subsystems["api › harness"]
    assert router.uses["api › harness"] == 2 and harness.used_by["api › router"] == 2
    assert router.summary == "Router package — pick a harness."
    assert "router/engine.py" not in [s[0] for s in router.symbols]   # file nodes aren't symbols


def test_community_labels_are_meaningful_never_placeholders():
    labels = community_labels(_graph(), project="Demo")
    assert labels[0] == "api › router" and labels[1] == "api › harness"
    assert labels[2].startswith("external")
    assert not any(re.fullmatch(r"Community \d+", v) for v in labels.values())


def test_render_is_one_note_per_subsystem_with_resolvable_links():
    notes = wiki.render(_graph(), project="Demo", meta={"head": "abc123", "branch": "dev"})
    assert set(notes) == {"Subsystems/api › router.md", "Subsystems/api › harness.md",
                          wiki.OVERVIEW}
    titles = {p.rsplit("/", 1)[1][:-3] for p in notes}
    for text in notes.values():
        assert text.startswith("---\n" + wiki.MARKER)
        for target in re.findall(r"\[\[([^\]|]+)", text):
            assert target in titles, target
    assert "[[api › harness]] — 2 references" in notes["Subsystems/api › router.md"]


def test_titles_never_become_hidden_dotfiles():
    graph = {"nodes": [_node(f"n{i}", f"f{i}()", f".cuttle/scripts/s{i}.py") for i in range(3)],
             "links": []}
    assert "cuttle › scripts" in build_view(graph, "Demo").subsystems


def test_write_replaces_generated_notes_and_never_touches_manual_ones(tmp_path):
    notes = wiki.render(_graph(), project="Demo")
    manual = tmp_path / "Subsystems" / "My thoughts.md"
    manual.parent.mkdir(parents=True)
    manual.write_text("# mine\n")
    shadow = tmp_path / "Subsystems" / "api › router.md"
    shadow.write_text("# hand-written router note\n")

    stats = wiki.write(tmp_path, notes)
    assert stats["kept_manual"] == 1
    assert shadow.read_text() == "# hand-written router note\n"

    stale = tmp_path / "Subsystems" / "old subsystem.md"
    stale.write_text("---\n" + wiki.MARKER + "\n---\n# old\n")
    again = wiki.write(tmp_path, notes)
    assert again["removed"] == 1 and not stale.exists()
    assert again["unchanged"] == len(notes) - 1
    assert manual.read_text() == "# mine\n"
