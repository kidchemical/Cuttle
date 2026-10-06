"""Phase 7 enforcement: owned-layer direction, coordinator funnel, manifests.

These tests reject demonstrated forbidden wiring, not style:

1. No production module may import the Flask monolith ``web_chat_api``
   (owned layers must never depend upward). Exactly two justifications
   exist: the doctor importability probe, and the dev-only shadow
   composition bootstrap (spawned as a child process, never imported by
   owned layers). The scanner fails loudly on production syntax errors
   and covers aliased dynamic imports.
2. The ``src/api`` internal import graph is acyclic at module level.
   Function-level and ``TYPE_CHECKING`` imports are deferred by design
   (documented, counted, allowed) — they break cycles intentionally.
3. The non-lane compat entry submits through ``chat_coordinator`` —
   proven at runtime with a fake submit, not source strings.
4. The SSE pump actually invokes the injected run and releases the busy
   slot — proven by draining a real pump; the release assertion runs
   before any test cleanup, and a neutering run proves it is load-bearing.
5. The real HTTP route ``POST /api/chat`` streams through the shared
   coordinator entry to the injected executor — a bypass of submit would
   fail the spy assertion.
6. Every bundled manifest satisfies the identity schema and carries no
   executable-install flags (BYO-CLI retirement enforcement).
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# Fail-closed execution guard (auto-applied to every test in this file).
# Any path that reaches a real harness CLI spawn or router provider call
# without an injected fake raises HERE — before subprocess/network — with
# a counter proving the attempt was blocked, not silently skipped. This
# is the backstop behind the per-test fakes (spend flags alone cannot
# stop a local spawn: a starred-slash default once drove a real Cursor
# CLI attempt that died on sandbox EROFS with no spend — disclosed, and
# now impossible to repeat silently).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_real_execution(monkeypatch):
    from api.agent_harness import kernel as _kernel
    from api.agent_router import dispatch as _dispatch
    from api.agent_router import integration as _integration

    calls = {"kernel": 0, "dispatch": 0}

    def _blocked_kernel(*args, **kwargs):
        calls["kernel"] += 1
        raise AssertionError(
            "fail-closed test guard: real harness CLI execution attempted "
            f"({args[0] if args else '?'}); inject a fake executor instead"
        )

    def _blocked_dispatch(*args, **kwargs):
        calls["dispatch"] += 1
        raise AssertionError(
            "fail-closed test guard: real router execution attempted; "
            "inject a fake instead"
        )

    monkeypatch.setattr(_kernel, "run_agent_web_command", _blocked_kernel)
    monkeypatch.setattr(_dispatch, "execute_decision", _blocked_dispatch)
    monkeypatch.setattr(_integration, "execute_decision", _blocked_dispatch)
    monkeypatch.setattr(
        _integration, "execute_explicit_target", _blocked_dispatch
    )
    return calls


def test_execution_guard_blocks_real_runners(_no_real_execution):
    """The guard — not spend flags — stops unmocked execution locally."""
    from api.agent_harness import kernel as _kernel
    from api.agent_router import dispatch as _dispatch
    from api.agent_router import integration as _integration

    for fn, args in (
        (_kernel.run_agent_web_command, ("cursor", "hi", "guard-sid")),
        (_dispatch.execute_decision, (object(),)),
        (_integration.execute_decision, (object(),)),
        (_integration.execute_explicit_target, (object(),)),
    ):
        with pytest.raises(AssertionError, match="fail-closed test guard"):
            fn(*args)
    assert _no_real_execution == {"kernel": 1, "dispatch": 3}


REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = REPO_ROOT / "src" / "api"

# file -> reason. Any new importer fails loudly with its path.
REVERSE_IMPORT_ALLOWLIST = {
    "src/api/doctor.py": (
        "importability health probe: try/except import, no attribute use, "
        "reports chat_backend ok/fail in /api/doctor"
    ),
    "src/scripts/cuttle_shadow_app.py": (
        "dev-only shadow composition bootstrap: spawned child imports the "
        "real app after installing deny guards; never imported by owned "
        "layers (api.dev_instance spawns it as a subprocess)"
    ),
}


def _web_chat_api_refs(tree: ast.AST):
    """Yield (lineno, kind) for real imports of web_chat_api.

    AST-based: comments and unrelated string literals (process regexes,
    log lines) never match. Covers static forms plus dynamic imports
    through module aliases (``il.import_module``) and function aliases
    bound in the same file (``from importlib import import_module as
    load``, ``from builtins import __import__ as _bi``, ``_imp =
    __import__``).

    Static limitations (documented, not analyzed): only per-file,
    scope-insensitive bindings are resolved — no flow analysis, no
    cross-module tracking, no conditional/shadowed-scope reasoning.
    ``getattr(importlib, "import_module")(...)`` and non-literal module
    names (variables, f-strings, concatenation) are not flagged: they are
    statically undecidable without false-positiving legitimate dynamic
    loaders (e.g. the agent catalog adapter loader).
    """
    # Pass 1 (per-file, scope-insensitive): collect local aliases for the
    # dynamic import entry points. Rebinding after use still counts — this
    # is a tripwire, and a false alarm is cheaper than a silent bypass.
    dynamic_names = {"import_module", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in ("importlib", "builtins"):
                for alias in node.names:
                    if alias.name in ("import_module", "__import__"):
                        dynamic_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            # Only the simple form ``alias = import_module`` /
            # ``alias = __import__`` (single Name target, Name value).
            if len(node.targets) != 1:
                continue
            target = node.targets[0]
            value = node.value
            if (
                isinstance(target, ast.Name)
                and isinstance(value, ast.Name)
                and value.id in ("import_module", "__import__")
            ):
                dynamic_names.add(target.id)
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
            if isinstance(func, ast.Attribute) and func.attr in (
                "import_module",
                "__import__",
            ):
                dynamic = True
            elif (
                isinstance(func, ast.Name) and func.id in dynamic_names
            ):
                dynamic = True
            else:
                dynamic = False
            if not dynamic:
                continue
            for arg in node.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and "web_chat_api" in arg.value
                ):
                    yield node.lineno, "dynamic-import"


def _parse_production(path: Path) -> ast.AST:
    """Parse or raise: a production file that does not parse is a finding,
    never a silent skip."""
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except OSError as exc:
        pytest.fail(f"cannot read production file {path}: {exc}")
    except SyntaxError as exc:
        pytest.fail(f"production syntax error in {path}: {exc}")


def _production_files():
    for root in ("src/api", "src/core", "src/managers", "src/scripts"):
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
        refs = list(_web_chat_api_refs(_parse_production(path)))
        if refs:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = refs
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
        tree = _parse_production(REPO_ROOT / rel)
        assert list(_web_chat_api_refs(tree)), f"stale allowlist entry: {rel}"


def test_reverse_import_scanner_discrimination(tmp_path):
    """The scanner catches every import form and ignores non-imports."""
    guilty = (
        "import api.web_chat_api\n"
        "from api import web_chat_api\n"
        "from api.web_chat_api import app\n"
        "import importlib as il\n"
        "x = il.import_module('api.web_chat_api')\n"
        "from importlib import import_module\n"
        "y = import_module('api.web_chat_api')\n"
        "z = __import__('api.web_chat_api')\n"
        "from importlib import import_module as load\n"
        "w = load('api.web_chat_api')\n"
        "from builtins import __import__ as _bi\n"
        "v = _bi('api.web_chat_api')\n"
        "_imp = __import__\n"
        "u = _imp('api.web_chat_api')\n"
    )
    tree = ast.parse(guilty)
    kinds = sorted(kind for _, kind in _web_chat_api_refs(tree))
    assert kinds == [
        "dynamic-import",
        "dynamic-import",
        "dynamic-import",
        "dynamic-import",
        "dynamic-import",
        "dynamic-import",
        "from-api-import",
        "from-import",
        "import",
    ]
    innocent = (
        "# import api.web_chat_api in a comment\n"
        'PATTERN = r"(?:web_chat_api|cuttle_daemon)"\n'
        'print("web_chat_api starting")\n'
        "import api.chat_coordinator\n"
    )
    assert list(_web_chat_api_refs(ast.parse(innocent))) == []
    # Aliased entry points called with anything but the monolith stay
    # silent — including non-literal names, which the scanner documents
    # as a static limitation rather than flagging.
    aliased_innocent = (
        "from importlib import import_module as load\n"
        "a = load('api.chat_coordinator')\n"
        "b = load(mod_name)\n"
        "c = load(f'api.{mod}')\n"
    )
    assert list(_web_chat_api_refs(ast.parse(aliased_innocent))) == []


def test_production_scan_covers_src_core():
    """src/core is production code inside the boundary, not exempt."""
    core_files = [
        str(p.relative_to(REPO_ROOT))
        for p in _production_files()
        if p.relative_to(REPO_ROOT).parts[:2] == ("src", "core")
    ]
    assert core_files, "reverse-import scan must cover src/core"


# --------------------------------------------------------------------------
# Import graph: module-level edges must be acyclic; deferred edges are
# allowed by design (they execute lazily and intentionally break cycles).
# --------------------------------------------------------------------------


def _scan_root(api_root: Path):
    """Return (modules, module_edges, deferred_edges) for one api/ root.

    Module-level = executes at import (top level, class bodies,
    decorators, base classes). Deferred = inside any function/lambda or
    under ``if TYPE_CHECKING:``. ``from api import foo`` adds an
    ``api.foo`` edge; ``from . import sib`` resolves through the real
    package context (``__init__.py`` modules are their own package).
    """

    def modname(path: Path):
        rel = path.relative_to(api_root).with_suffix("")
        parts = list(rel.parts)
        is_init = parts[-1] == "__init__"
        if is_init:
            parts = parts[:-1]
        return "api." + ".".join(parts), is_init

    def resolve(mod, is_init, level, module):
        parts = mod.split(".")
        pkg = parts if is_init else parts[:-1]
        rise = level - 1
        if rise > len(pkg) - 1:
            return None
        base = pkg[: len(pkg) - rise]
        if module:
            return ".".join(base + [module])
        return ".".join(base)

    mods = {}
    for path in sorted(api_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name, is_init = modname(path)
        mods[name] = (is_init, ast.parse(path.read_text(encoding="utf-8")))

    known = set(mods)
    medges, fedges = {}, {}
    for mod, (is_init, tree) in mods.items():
        me, fe = set(), set()
        stack = [(tree, False, False)]  # node, in_function, in_typechecking
        while stack:
            node, inf, itc = stack.pop()
            for child in ast.iter_child_nodes(node):
                ninf = inf or isinstance(
                    child,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
                )
                nitc = itc
                if isinstance(child, ast.If):
                    test = child.test
                    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
                        nitc = True
                stack.append((child, ninf, nitc))
            target = None
            extra = []
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    if node.module:
                        target = resolve(mod, is_init, node.level, node.module)
                    else:
                        # `from . import sib`: submodule(s) of the package.
                        base = resolve(mod, is_init, node.level, None)
                        if base:
                            extra = [
                                base + "." + a.name
                                for a in node.names
                                if a.name != "*"
                            ]
                elif node.module == "api":
                    extra = [
                        "api." + a.name
                        for a in node.names
                        if a.name != "*"
                    ]
                elif node.module and node.module.startswith("api."):
                    target = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("api."):
                        (fe if (inf or itc) else me).add(alias.name)
            for edge in ([target] if target else []) + extra:
                (fe if (inf or itc) else me).add(edge)
        me.discard(mod)
        fe.discard(mod)
        medges[mod] = {e for e in me if e in known}
        fedges[mod] = {e for e in fe if e in known}
    return mods, medges, fedges


def _find_cycles(medges):
    visiting, done, cycles = set(), set(), []

    def visit(mod, stack):
        visiting.add(mod)
        for dep in sorted(medges.get(mod, ())):
            if dep in visiting:
                cycles.append(" -> ".join(stack + [dep]))
            elif dep not in done:
                visit(dep, stack + [dep])
        visiting.discard(mod)
        done.add(mod)

    for mod in sorted(medges):
        if mod not in done:
            visit(mod, [mod])
    return cycles


def _write_fixture_pkg(root: Path, files: dict):
    pkg = root / "api"
    for rel, src in files.items():
        path = pkg / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")


def test_graph_detects_two_node_absolute_cycle(tmp_path):
    _write_fixture_pkg(
        tmp_path,
        {"a.py": "from api.b import X\n", "b.py": "from api.a import Y\n"},
    )
    _, medges, _ = _scan_root(tmp_path / "api")
    cycles = _find_cycles(medges)
    assert len(cycles) == 1 and "api.a" in cycles[0] and "api.b" in cycles[0]


def test_graph_detects_relative_init_cycle(tmp_path):
    _write_fixture_pkg(
        tmp_path,
        {
            "pkg/__init__.py": "from api.pkg.engine import run\n",
            "pkg/engine.py": "from api.pkg import helpers as h\n",
            "pkg/helpers.py": "VALUE = 1\n",
        },
    )
    _, medges, _ = _scan_root(tmp_path / "api")
    cycles = _find_cycles(medges)
    assert any(
        "api.pkg" in c and "api.pkg.engine" in c for c in cycles
    ), cycles


def test_graph_accepts_diamond_with_all_forms(tmp_path):
    _write_fixture_pkg(
        tmp_path,
        {
            "__init__.py": "",
            "leaf.py": "VALUE = 1\n",
            "mid.py": "from api.leaf import VALUE\n",
            "top.py": "from api import leaf\nfrom . import mid\n",
            "pkg/__init__.py": "from api.pkg.core import run\n",
            "pkg/core.py": "import api.leaf as leaf\n",
        },
    )
    mods, medges, _ = _scan_root(tmp_path / "api")
    assert _find_cycles(medges) == []
    # The resolver must actually see the edges it claims to cover.
    assert medges["api.top"] == {"api.leaf", "api.mid"}
    assert medges["api.pkg.core"] == {"api.leaf"}
    assert medges["api.pkg"] == {"api.pkg.core"}


def test_function_and_typechecking_imports_are_deferred(tmp_path):
    _write_fixture_pkg(
        tmp_path,
        {
            "a.py": "from api.b import X\n",
            "b.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n"
                "    from api.a import Y\n"
                "def f():\n"
                "    from api.a import Z\n"
            ),
        },
    )
    _, medges, fedges = _scan_root(tmp_path / "api")
    assert _find_cycles(medges) == []
    assert medges["api.b"] == set()
    assert fedges["api.b"] == {"api.a"}


def test_api_import_graph_acyclic():
    mods, medges, fedges = _scan_root(API_ROOT)
    assert len(mods) > 100  # the scan must actually cover the package
    assert sum(len(v) for v in medges.values()) > 100  # and see real edges
    cycles = _find_cycles(medges)
    assert not cycles, f"module-level import cycles in src/api: {cycles}"


def test_compat_entry_submits_through_coordinator(monkeypatch):
    """sessions_send-style entry reaches submit_agent_turn unclaimed (runtime)."""
    from api import web_chat_api as wca
    import api.chat_coordinator as coordinator

    seen = {}

    def fake_submit(turn, *, io, delivery, claim, **kwargs):
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


def _drain_with_deadline(gen, seconds=20):
    items = []
    deadline = time.time() + seconds
    for chunk in gen:
        items.append(chunk)
        if time.time() > deadline:
            pytest.fail("SSE pump did not terminate")
    return items


def test_sse_pump_invokes_injected_run_and_releases():
    """Drain a real _generate_chat_stream: status + response + release.

    The release assertion runs BEFORE any test cleanup: nothing after it
    can mask a pump that leaks the busy slot.
    """
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

    events = _drain_with_deadline(
        wca._generate_chat_stream(fake_run, sid, on_result=persisted.append)
    )

    texts = [c for c in events if c.startswith("data: ")]
    bodies = [json.loads(c[len("data: "):]) for c in texts]
    kinds = [b.get("type") for b in bodies]
    assert "status" in kinds
    responses = [b for b in bodies if b.get("type") == "response"]
    assert responses and responses[-1].get("response") == "pump-ok-fake"
    assert persisted and persisted[0].get("response") == "pump-ok-fake"
    # Release BEFORE cleanup: a leaking pump fails here.
    assert chat_delivery.try_begin(sid) is True
    # Cleanup only after every assertion has run.
    chat_delivery.end(sid)


def test_sse_release_assertion_is_load_bearing(monkeypatch):
    """Neutering delivery.end must flip the release check to busy.

    If the release assertion passed regardless of the pump's own release,
    this twin would pass too — it must instead observe the held slot.
    """
    from api import web_chat_api as wca
    from api import chat_delivery

    sid = "p7-sse-neutered"
    try:
        chat_delivery.end(sid)
    except Exception:
        pass
    real_end = chat_delivery.end
    monkeypatch.setattr(chat_delivery, "end", lambda *a, **k: None)

    def fake_run(status_queue=None):
        return {"success": True, "response": "neutered-fake"}

    # Unique sid: even if the drain raised, a leaked slot expires via TTL
    # and never collides with another test. No cleanup may run before the
    # assertions below — that would be the vacuous pattern under test.
    events = _drain_with_deadline(wca._generate_chat_stream(fake_run, sid))
    assert any('"neutered-fake"' in c for c in events)  # pump still completed
    # With end() neutered the slot stays held: proves the sibling test's
    # try_begin assertion observes the pump's release, not test cleanup.
    assert chat_delivery.try_begin(sid) is False
    monkeypatch.undo()
    real_end(sid)
    assert chat_delivery.try_begin(sid) is True
    real_end(sid)


def test_http_chat_sync_lane_goes_through_coordinator(monkeypatch, tmp_path):
    """Real POST /api/chat (sync lane) runs through submit_agent_turn.

    The spy forwards to the REAL coordinator implementation while the
    harness executor is faked: a route that bypassed submit would leave
    the spy empty and fail, while a broken executor would empty the body.
    """
    from api import web_chat_api as wca
    import api.chat_coordinator as coordinator

    real_submit = coordinator.submit_agent_turn
    calls = []

    def spy_submit(turn, *, io, delivery, claim, **kwargs):
        calls.append((type(turn).__name__, claim))
        return real_submit(turn, io=io, delivery=delivery, claim=claim, **kwargs)

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        return {
            "success": True,
            "response": "http-funnel-fake",
            "type": "fake",
            "agent_id": agent_id,
        }

    monkeypatch.setattr(coordinator, "submit_agent_turn", spy_submit)
    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "p7_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    res = client.post(
        "/api/chat",
        json={
            "message": "/cursor hello http",
            "session_id": "p7-http-1",
            "stream": False,
        },
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["response"] == "http-funnel-fake"
    assert calls, "sync lane bypassed chat_coordinator.submit_agent_turn"
    assert calls[0][0] == "PreparedAgentTurn"


def test_http_chat_stream_goes_through_shared_entry(
    monkeypatch, tmp_path
):
    """P5-E: real POST /api/chat streams through submit_agent_stream_turn.

    The spy forwards to the REAL stream entry while the harness executor
    is faked: a lane that bypassed the shared entry would leave the spy
    empty and fail. Same executor, same delivery lifecycle as sync —
    only the lifecycle events (not a body) come back.
    """
    from api import web_chat_api as wca
    import api.chat_coordinator as coordinator

    real_submit = coordinator.submit_agent_stream_turn
    calls = []

    def spy_submit(turn, *, io, delivery, **kwargs):
        calls.append(type(turn).__name__)
        yield from real_submit(turn, io=io, delivery=delivery, **kwargs)

    ran = []

    def fake_run(agent_id, prompt, chat_session_id, **kwargs):
        ran.append((agent_id, prompt))
        return {"success": True, "response": "http-stream-fake", "type": "fake"}

    monkeypatch.setattr(coordinator, "submit_agent_stream_turn", spy_submit)
    monkeypatch.setattr(wca, "_run_pinned_harness_turn", fake_run)
    from api.auth_db import AuthDatabase

    db = AuthDatabase(tmp_path / "p7b_auth.db")
    owner = db.create_user("owner@local", "Owner", "local", password="x")
    token = db.create_auth_session(owner)
    monkeypatch.setattr("api.auth_api.get_auth_db", lambda: db)
    monkeypatch.setattr("api.auth_db.get_auth_db", lambda: db)
    client = wca.app.test_client()
    client.set_cookie("session_token", token)
    res = client.post(
        "/api/chat",
        json={"message": "/cursor hello stream", "session_id": "p7-http-2"},
    )
    assert res.status_code == 200
    assert "http-stream-fake" in res.get_data(as_text=True)
    # Same shared executor served the stream…
    assert ran == [("cursor", "hello stream")]
    # …through the shared stream entry, not around it.
    assert calls == ["PreparedAgentTurn"]


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
