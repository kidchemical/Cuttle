"""Opt-in AI subsystem explanations, full docstrings and note layout (no model calls)."""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from api.knowledge_graph import enrich, service, sources, wiki
from api.knowledge_graph.protocol import GraphStore
from api.knowledge_graph.resolve import ResolvedContext
from api.knowledge_graph.subsystems import build_view
from api.model_providers.types import TextResult

ROUTER_DOC = "A long router docstring. It explains routing across several sentences " * 4

FILES = {
    "src/api/router/__init__.py": f'"""{ROUTER_DOC}\n\nSecond paragraph."""\n',
    "src/api/router/engine.py": textwrap.dedent('''\
        """Core routing engine — decide a target."""
        from api.harness.run import run_agent


        def decide(turn):
            """Pick a harness."""
            return run_agent(turn)
        '''),
    "src/api/router/types.py": '"""Typed router entities."""\nclass Decision:\n    pass\n',
    "src/api/harness/run.py": '"""Harness entry."""\ndef run_agent(turn):\n    return turn\n',
    "src/api/harness/kernel.py": '"""Kernel."""\nclass Kernel:\n    pass\n',
    "src/api/harness/types.py": '"""Harness types."""\nclass AgentResult:\n    pass\n',
    "docs/guides/ROUTER.md": "# Router\n\n## Fallback\n\nNever-ran failures walk the chain.\n",
    "AGENTS.md": "# Brief\n\n## Unrelated\n\nNothing here.\n\n## Routing\n\nThe `src/api/router/` package picks harnesses.\n",
}


def _graph():
    def n(nid, label, path, loc, community=0, ftype="code"):
        return {"id": nid, "label": label, "source_file": path, "source_location": loc,
                "file_type": ftype, "community": community}
    nodes = [
        n("r_init", "A long router docstring. It explains routing…", "src/api/router/__init__.py", "L1",
          ftype="rationale"),
        n("r_decide", "decide()", "src/api/router/engine.py", "L5"),
        n("r_types", "Decision", "src/api/router/types.py", "L2"),
        n("h_run", "run_agent()", "src/api/harness/run.py", "L2", 1),
        n("h_kernel", "Kernel", "src/api/harness/kernel.py", "L2", 1),
        n("h_types", "AgentResult", "src/api/harness/types.py", "L2", 1),
    ]
    links = [{"source": "r_decide", "target": "h_run", "relation": "calls"},
             {"source": "r_decide", "target": "r_types", "relation": "references"},
             {"source": "h_run", "target": "r_decide", "relation": "calls"}]
    return {"nodes": nodes, "links": links}


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def _view(root):
    graph = _graph()
    docs = sources.module_docstrings(root, [f for f in FILES if f.endswith(".py")])
    return graph, build_view(graph, "Demo", docs)


def test_full_docstrings_replace_truncated_labels_and_note_puts_prose_first(repo):
    graph, view = _view(repo)
    router = view.subsystems["api › router"]
    assert router.summary.startswith(ROUTER_DOC.strip()[:40]) and "…" not in router.summary
    note = wiki.render(graph, project="Demo", view=view)["Subsystems/api › router.md"]
    assert "\n# " not in note                       # Obsidian shows the title already
    assert "Second paragraph" not in note           # summary = first paragraph only
    assert note.index("## Modules") < note.index("## Depends on") < note.index("## Key symbols")
    assert "— Core routing engine — decide a target." in note


def test_inputs_prioritise_docs_and_fingerprint_tracks_only_real_inputs(repo):
    graph, view = _view(repo)
    sub = view.subsystems["api › router"]
    docs = service._project_docs(str(repo))
    inp = enrich.build_input(view, graph, sub, repo, docs, "Demo")
    assert inp.docs[0] == "docs/guides/ROUTER.md"   # doc named after the subsystem, in full
    assert "## Routing" in inp.prompt and "## Unrelated" not in inp.prompt
    assert inp.prompt.index("# Documentation") < inp.prompt.index("# Code excerpts")
    assert "5|     \"\"\"Pick a harness.\"\"\"" not in inp.prompt  # numbered lines…
    assert '6|     """Pick a harness."""' in inp.prompt         # …from the real file
    again = enrich.build_input(view, graph, sub, repo, docs, "Demo")
    assert again.fingerprint == inp.fingerprint

    (repo / "src/api/harness/kernel.py").write_text('"""Kernel."""\n\n\nclass Kernel:\n    pass\n')
    assert enrich.build_input(view, graph, sub, repo, docs, "Demo").fingerprint == inp.fingerprint
    (repo / "src/api/router/engine.py").write_text(FILES["src/api/router/engine.py"] + "# edit\n")
    assert enrich.build_input(view, graph, sub, repo, docs, "Demo").fingerprint != inp.fingerprint


def _input(repo):
    graph, view = _view(repo)
    sub = view.subsystems["api › router"]
    return enrich.build_input(view, graph, sub, repo, service._project_docs(str(repo)), "Demo")


def test_citations_record_grounding_support_and_existence(repo):
    inp = _input(repo)
    md = ("`decide` hands the turn to `run_agent` [src/api/router/engine.py:L7].\n"
          "- It also manages billing quotas [src/api/router/engine.py:L7].\n"
          "- The kernel is here [src/api/harness/kernel.py:L3].\n"
          "- Gone [src/api/router/engine.py:L999]; never-ran failures walk the chain "
          "[docs/guides/ROUTER.md#Fallback]; [AGENTS.md#Missing].")
    recs = enrich.assess_citations(md, repo, inp)
    assert [(r["exists"], r["grounded"], r["supported"]) for r in recs] == [
        (True, True, True),      # shown in the excerpt, names the code on that line
        (True, True, False),     # real line, but nothing there about quotas
        (True, False, False),    # kernel.py was never shown to the model
        (False, False, False),
        (True, True, True),
        (False, False, False),
    ]
    assert recs[0]["anchor"] == {"text": "return run_agent(turn)", "symbol": "decide", "offset": 2}
    out, counts = enrich.render_citations(md, repo, recs)
    assert counts == {"total": 6, "supported": 2, "unsupported": 2, "moved": 0, "outdated": 2}
    assert out.count("<sup>?</sup>") == 2 and "`AGENTS.md § Missing` ⚠" in out


def test_citations_follow_moved_code_and_flag_changed_code(repo):
    inp = _input(repo)
    md = "`decide` hands the turn to `run_agent` [src/api/router/engine.py:L7]."
    recs = enrich.assess_citations(md, repo, inp)
    engine = repo / "src/api/router/engine.py"
    engine.write_text(engine.read_text().replace("from api.harness.run import run_agent",
                                                 "import os\nfrom api.harness.run import run_agent"))
    out, counts = enrich.render_citations(md, repo, recs)
    assert counts["moved"] == 1 and "[src/api/router/engine.py:L8](" in out
    engine.write_text(engine.read_text().replace("return run_agent(turn)", "return None"))
    out, counts = enrich.render_citations(md, repo, recs)
    assert counts["outdated"] == 1 and "`src/api/router/engine.py:L7` ⚠" in out


def test_secondary_docs_contribute_only_lines_that_name_the_subsystem(repo):
    (repo / "README.md").write_text("# Demo\n\n## Features\n\nLots of generic text.\n"
                                    "The router picks a harness.\nMore generic text.\n\n"
                                    "## Layout\n\n| `src/api/router/` | routing |\n\n"
                                    "## Router internals\n\nKept whole: the heading names it.\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    inp = _input(repo)
    assert "The router picks a harness." in inp.prompt and "generic text" not in inp.prompt
    assert "## Router internals\nKept whole: the heading names it." in inp.prompt


def test_clean_output_keeps_only_level3_sections():
    raw = "```markdown\n## How it works\n## Purpose\nRoutes.\n### Execution flow\n1. a\n```"
    assert enrich.clean_output(raw) == "### Purpose\nRoutes.\n### Execution flow\n1. a"


class _Provider:
    id = "fake"

    def is_indexed(self, store):
        return True

    def graph_data(self, store):
        return _graph()


def test_enrich_is_opt_in_cached_and_marks_stale(repo, tmp_path, monkeypatch):
    store = GraphStore("fake", tmp_path / "store", "demo", "primary", {"id": 1, "name": "Demo"},
                       str(repo), None)
    ctx = ResolvedContext({"id": 1, "name": "Demo", "path": str(repo)}, str(repo), None)
    monkeypatch.setattr(service, "provider_for", lambda pid=None: _Provider())
    monkeypatch.setattr(service, "store_for", lambda c, pid=None: store)
    monkeypatch.setenv("CUTTLE_OBSIDIAN_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    calls = []

    def fake_generate(task, request, *, chain=None, cancel_event=None):
        calls.append((task, chain))
        return TextResult("### Purpose\n`decide` hands the turn to `run_agent` [src/api/router/engine.py:L7].\n"
                          "### Fallback and failure behavior\nNever-ran failures walk the chain "
                          "[docs/guides/ROUTER.md#Fallback].\n"
                          "### Documentation vs code\nThe docs never mention `Decision` [src/api/router/types.py:L2].",
                          {"model": "opus"})
    import api.background_helpers as bg
    monkeypatch.setattr(bg, "generate", fake_generate)

    # plain export never calls a model and renders no AI section
    note = tmp_path / "vault" / "Demo" / "Subsystems" / "api › router.md"
    service.export_obsidian(ctx)
    assert not calls and enrich.BEGIN not in note.read_text()

    assert service.enrich(ctx, "api/router", via="cli:claude", dry_run=True)["status"] == "would_generate"
    with pytest.raises(service.KnowledgeGraphError, match="no executor"):
        service.enrich(ctx, "api › router")             # no --via, no configured wiki chain
    assert not calls

    out = service.enrich(ctx, "api › router", via="cli:claude", model="opus")
    assert out["status"] == "generated"
    assert out["citations"]["total"] == 3 and out["citations"]["supported"] == 3
    assert calls == [("wiki", [{"kind": "cli", "target": "claude", "model": "opus", "effort": None,
                                "temperature": None, "auth": "subscription"}])]
    text = note.read_text()
    assert enrich.BEGIN in text and "Out of date" not in text
    assert text.index("## How it works") < text.index("## Documentation vs code") < text.index(enrich.END)
    assert text.index(enrich.END) < text.index("## Modules")
    assert "nothing is changed automatically" in text and "<sup>?</sup>" not in text

    assert service.enrich(ctx, "api › router", via="cli:claude")["status"] == "unchanged"
    assert len(calls) == 1                              # unchanged inputs: no second call

    (repo / "src/api/router/engine.py").write_text(FILES["src/api/router/engine.py"] + "# edit\n")
    service.export_obsidian(ctx)
    stale = note.read_text()
    assert "Out of date" in stale and "hands the turn" in stale
    assert len(calls) == 1                              # export never regenerates

    listed = service.list_notes(ctx)
    assert listed["ok"] and listed["notes"][0]["status"] == "stale"
    retrieved = service.retrieve_note(ctx, "api › router")
    assert retrieved["ok"] and retrieved["status"] == "stale"
    assert "out of date" in retrieved["warning"].lower()
    assert "file://" not in retrieved["explanation"]
    assert "[[Overview]]" not in retrieved["explanation"]
    assert "[!warning]" not in retrieved["explanation"]
    assert "<sup>" not in retrieved["explanation"]
    assert "[src/api/router/engine.py:L7]" in retrieved["explanation"]


def test_critical_facts_detect_omissions_and_are_not_silent():
    empty = "### Purpose\nRoutes turns.\n"
    missing = enrich.missing_facts("src/api/agent_router", empty)
    assert {f["id"] for f in missing} == {"cancellation_is_terminal", "chat_entry_star"}
    complete = (empty + "Cancellation is terminal: a [CANCELLED] result never escalates.\n"
                "The star is applied server-side in /api/chat so starred agents still run.")
    assert enrich.missing_facts("src/api/agent_router", complete) == []
    assert enrich.missing_facts("src/api/router", empty) == []  # unknown key: no checklist


def test_agent_view_marks_outdated_citations_without_obsidian_chrome(repo):
    inp = _input(repo)
    md = ("`decide` hands the turn to `run_agent` [src/api/router/engine.py:L7].\n"
          "Gone [src/api/router/engine.py:L999].")
    recs = enrich.assess_citations(md, repo, inp)
    entry = {"key": "src/api/router", "title": "api › router", "fingerprint": "x",
             "markdown": md, "citations": recs, "docs": ["docs/guides/ROUTER.md"],
             "generated_at": "2026-10-23T00:00:00Z", "executor": {"target": "cli:claude"}}
    view = enrich.agent_view(entry, "x", repo)
    assert view["status"] == "current"
    assert "(outdated)" in view["explanation"]
    assert view["outdated_citations"] == ["src/api/router/engine.py:L999"]
    assert "no longer resolve" in view["warning"]
    assert "file://" not in view["explanation"] and "<sup>" not in view["explanation"]

    stale = enrich.agent_view(entry, "other", repo)
    assert stale["status"] == "stale" and "out of date" in stale["warning"].lower()

    routed = dict(entry, key="src/api/agent_router", markdown=md)
    flagged = enrich.agent_view(routed, "x", repo)
    assert {f["id"] for f in flagged["missing_facts"]} == {
        "cancellation_is_terminal", "chat_entry_star"}
    assert "Required behavioral facts are missing" in flagged["warning"]


def test_retrieve_missing_note_is_explicit(repo, tmp_path, monkeypatch):
    store = GraphStore("fake", tmp_path / "store", "demo", "primary", {"id": 1, "name": "Demo"},
                       str(repo), None)
    ctx = ResolvedContext({"id": 1, "name": "Demo", "path": str(repo)}, str(repo), None)
    monkeypatch.setattr(service, "provider_for", lambda pid=None: _Provider())
    monkeypatch.setattr(service, "store_for", lambda c, pid=None: store)
    out = service.retrieve_note(ctx, "api › router")
    assert out["ok"] is False and out["status"] == "missing"
    assert "enrich" in out["hint"]
    assert service.list_notes(ctx)["notes"] == []
