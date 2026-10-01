"""Phase 7 enforcement: owned-layer direction, coordinator funnel, manifests.

These tests reject demonstrated forbidden wiring, not style:

1. No production module may import the Flask monolith ``web_chat_api``
   (owned layers must never depend upward). Exactly one justification
   exists: the doctor importability probe.
2. The ``src/api`` internal import graph is acyclic (module-level edges).
3. The non-lane compat entry submits through ``chat_coordinator`` —
   proven at runtime with a fake submit, not source strings.
4. The SSE pump actually invokes the injected run and publishes its
   result — proven by draining a real pump with a fake runner.
5. Every bundled manifest satisfies the identity schema and carries no
   executable-install flags (BYO-CLI retirement enforcement).
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "src" / "api"

# file -> reason. Any new importer fails loudly with its path.
REVERSE_IMPORT_ALLOWLIST = {
    "src/api/doctor.py": (
        "importability health probe: try/except import, no attribute use, "
        "reports chat_backend ok/fail in /api/doctor"
    ),
}


def _web_chat_api_refs(tree: ast.AST):
    """Yield (lineno, kind) for real imports of web_chat_api.

    AST-based: comments and unrelated string literals (process regexes,
    log lines) never match. Covers static and dynamic import forms.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("web_chat_api", "api.web_chat_api"):
                    yield node.lineno, "import"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "web_chat_api" or mod == "api.web_chat_api":
                yield node.lineno, "from-import"
            elif mod == "api" and any(
                a.name == "web_chat_api" for a in node.names
            ):
                yield node.lineno, "from-api-import"
        elif isinstance(node, ast.Call):
            func = node.func
            is_dynamic = (
                isinstance(func, ast.Attribute)
                and func.attr in ("import_module", "__import__")
            ) or (
                isinstance(func, ast.Name) and func.id == "__import__"
            )
            if not is_dynamic:
                continue
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and "web_chat_api" in arg.value
                ):
                    yield node.lineno, "dynamic-import"


def _production_files():
    for root in ("src/api", "src/managers", "src/scripts"):
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def test_no_reverse_imports_into_web_chat_api():
    offenders = {}
    for path in _production_files():
        if path.name == "web_chat_api.py":
            continue  # the monolith itself
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        refs = list(_web_chat_api_refs(tree))
        if refs:
            offenders[str(path.relative_to(REPO_ROOT))] = refs
    unexpected = {
        f: refs
        for f, refs in offenders.items()
        if f not in REVERSE_IMPORT_ALLOWLIST
    }
    assert not unexpected, (
        "owned layers must not import the Flask monolith; "
        f"new reverse imports: {unexpected}. Either remove the dependency "
        f"or record an explicit justification in REVERSE_IMPORT_ALLOWLIST."
    )
    # The allowlist itself must stay accurate: every entry must still exist
    # and still reference the monolith (no stale blanket permission).
    for rel in REVERSE_IMPORT_ALLOWLIST:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        assert list(_web_chat_api_refs(tree)), f"stale allowlist entry: {rel}"


def _internal_edges():
    """Module-level api.* -> api.* edges (relative + absolute)."""
    mods = {}
    for path in sorted(API_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(API_ROOT).with_suffix("")
        parts = list(rel.parts)
        name = "api." + ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        try:
            mods[name] = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue

    def resolve(mod, node):
        parts = mod.split(".")
        if node.level > len(parts):
            return None
        base = parts[: len(parts) - node.level + 1]
        if node.module:
            return ".".join(base + [node.module])
        return ".".join(base)

    edges = {}
    for mod, tree in mods.items():
        out = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                target = resolve(mod, node)
                if target and target in mods:
                    out.add(target)
            elif isinstance(node, ast.ImportFrom) and node.module in (
                "api",
                "api.web_chat_api",
            ):
                out.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in mods:
                        out.add(alias.name)
        edges[mod] = out
    return edges


def test_api_import_graph_acyclic():
    edges = _internal_edges()
    assert len(edges) > 100  # the scan must actually cover the package
    visiting, done, cycles = set(), set(), []

    def visit(mod, stack):
        visiting.add(mod)
        for dep in edges.get(mod, ()):
            if dep in visiting:
                cycles.append(" -> ".join(stack + [dep]))
            elif dep not in done:
                visit(dep, stack + [dep])
        visiting.discard(mod)
        done.add(mod)

    for mod in edges:
        if mod not in done:
            visit(mod, [mod])
    assert not cycles, f"module-level import cycles in src/api: {cycles}"


def test_compat_entry_submits_through_coordinator(monkeypatch):
    """sessions_send-style entry reaches submit_agent_turn unclaimed (runtime)."""
    from api import web_chat_api as wca
    import api.chat_coordinator as coordinator

    seen = {}

    def fake_submit(turn, *, io, delivery, claim):
        seen["turn"] = turn
        seen["claim"] = claim
        seen["io"] = io
        return SimpleNamespace(body={"success": True, "response": "via-submit"})

    monkeypatch.setattr(coordinator, "submit_agent_turn", fake_submit)
    out = wca.process_message_with_bot(
        "/cursor do the funnel thing",
        "p7-funnel-1",
        session_kind="sessions_send",
        routing_key="sessions_send",
    )
    assert out["response"] == "via-submit"
    turn = seen["turn"]
    assert type(turn).__name__ == "PreparedAgentTurn"
    assert turn.session_kind == "sessions_send"
    assert seen["claim"] is False
    assert callable(seen["io"].run_harness)


def test_sse_pump_invokes_injected_run_and_releases(monkeypatch):
    """Drain a real _generate_chat_stream: status + response + release."""
    import json

    from api import web_chat_api as wca
    from api import chat_delivery

    sid = "p7-sse-1"
    try:
        chat_delivery.end(sid)  # clean slate even if a prior run leaked
    except Exception:
        pass
    # NOTE: do NOT try_begin here — the pump under test claims the busy
    # slot itself; a pre-claim would (correctly) yield a busy event.

    persisted = []

    def fake_run(status_queue=None):
        status_queue.put(("status", "Working"))
        return {"success": True, "response": "pump-ok-fake"}

    events = []
    deadline = time.time() + 20
    try:
        for chunk in wca._generate_chat_stream(
            fake_run, sid, on_result=persisted.append
        ):
            events.append(chunk)
            if time.time() > deadline:
                pytest.fail("SSE pump did not terminate")
    finally:
        try:
            chat_delivery.end(sid)
        except Exception:
            pass

    texts = [c for c in events if c.startswith("data: ")]
    bodies = [json.loads(c[len("data: "):]) for c in texts]
    kinds = [b.get("type") for b in bodies]
    assert "status" in kinds
    responses = [b for b in bodies if b.get("type") == "response"]
    assert responses and responses[-1].get("response") == "pump-ok-fake"
    assert persisted and persisted[0].get("response") == "pump-ok-fake"
    # The pump released the busy slot: re-acquirable, no stuck spinner.
    assert chat_delivery.try_begin(sid) is True
    chat_delivery.end(sid)


def test_bundled_manifest_schema_and_no_install_flags():
    """Every shipped manifest: canonical identity + guidance-only install."""
    from api.agent_harness import catalog

    manifests = catalog.list_agent_manifests()
    assert len(manifests) >= 8
    for manifest in manifests:
        if manifest.source != "bundled":
            continue
        problem = catalog._identity_error(manifest.id, manifest.slash)
        assert problem is None, f"{manifest.id}: {problem}"
        assert manifest.auto_install is False, manifest.id
        assert manifest.install_kind in ("", "npm_global", "script_url"), manifest.id
