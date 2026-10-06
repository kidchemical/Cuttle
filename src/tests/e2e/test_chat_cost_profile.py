"""Bounded repeatable browser cost profile (F1 measurement).

Opt-in only: set CUTTLE_COST_PROFILE=1. Not a benchmark framework and not a
performance gate: no timing thresholds, no p95. Three fresh browser contexts
per scenario; median/min/max of observed timings plus request counts and JSON
payload bytes by endpoint, split into load vs steady windows.

What this measures: client-side request counts/bytes against a deterministic
fake API, transcript DOM growth, PerformanceObserver longtask samples, initial
window readiness vs explicit full-history paging, and steady-state polling.

A separate scenario measures real AuthDatabase queries on private SQLite
fixtures. Shell history supplies distinct session IDs sharing one project.
Additional scenarios exercise synthetic visibility and native stream recovery.
This does NOT measure real vendor latency, full authenticated-route database
cost, live timer/listener counts, action-card costs or CDN-loaded highlighting.
Request counts establish client traffic, not server cache performance.

Guards: same-origin static fixtures + fake API only, via the existing
apply_request_guard/static_server/browser fixtures (relative import, no
copies, no sys.path hacks). Every scenario asserts workload readiness
(expected rows rendered, expected text present) and zero page errors so an
empty page cannot count as fast. Production windowing (newest PAGE_SIZE=10
first, scroll-up older pages) is honored: initial window readiness is
reported separately from explicit paging, and driver elapsed/paging/settle
waits are labeled as such — never presented as render cost.
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
from pathlib import Path

import pytest

from .test_shared_diff_modal import (  # noqa: F401  (reuse existing fixtures)
    apply_request_guard,
    browser,
    static_server,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("CUTTLE_COST_PROFILE") != "1",
    reason="opt-in profile: set CUTTLE_COST_PROFILE=1",
)

WEB = Path(__file__).resolve().parents[2] / "web"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("CUTTLE_COST_PROFILE_OUT") or (ROOT / "temp" / "f1-measurements.json"))

RUNS = 3
STEADY_S = 12  # steady-state polling window per run (Playwright-clock pumps)
PAGE_SIZE = 10  # production CHAT_HISTORY_PAGE_SIZE (chat_page.js)
QUIESCE_MS = 750
VIEWPORT = {"width": 1280, "height": 800}

PROJECT = {"id": 1, "name": "Cuttle", "path": "/repo/Cuttle", "type": "local"}


def build_history(n):
    """Deterministic N-row transcript: plain / code cycle (no action forms)."""
    rows = []
    for i in range(1, n + 1):
        if i % 2:
            content = (
                f"Plain status update {i}: checked the nightly sync and the queue "
                f"depth looks nominal across all three workers."
            )
        else:
            content = (
                f"Here is worker {i}:\n```python\ndef worker_{i}(queue):\n"
                + "\n".join(f"    step_{s} = poll(queue, {s})" for s in range(8))
                + f"\n    return run_all(step_0)\n```"
            )
        rows.append(
            {
                "id": i,
                "chat_session_id": 42,
                "role": "user" if i % 2 else "assistant",
                "content": content,
                "timestamp": "2026-10-01 12:00:00",
                "metadata": {},
            }
        )
    return rows


class CostAPI:
    """Deterministic fake API. Records requests; backend cost NOT measured."""

    def __init__(self, rows, session_projects=None):
        self.session_projects = session_projects or {}
        self.rows = rows
        self.requests = []

    def handle(self, route):
        from urllib.parse import parse_qs, urlparse

        req = route.request
        parsed = urlparse(req.url)
        query = parse_qs(parsed.query)
        path = parsed.path
        post = req.post_data or ""
        if path.endswith("/messages"):
            data = self._windowed(query)
            sid = int(path.split('/sessions/')[1].split('/')[0])
            data['messages'] = [dict(row, chat_session_id=sid) for row in data['messages']]
            project = self.session_projects.get(sid, PROJECT)
            data.update(project_id=project['id'], project_name=project['name'],
                        project_path=project['path'])
        elif path == '/api/chat-live-status-batch':
            ids = (query.get('session_ids') or [''])[0].split(',')
            data = {'success': True, 'statuses': {
                sid: {'active': False, 'generating': False, 'cancelled': False,
                      'status': '', 'subagents': [], 'widgets_revision': 0}
                for sid in ids if sid}}
        elif path == '/api/chat-live-status':
            data = {'success': True, 'active': False, 'generating': False,
                    'cancelled': False, 'status': '', 'subagents': [],
                    'widgets_revision': 0}
        elif path == '/api/git/pending-changes':
            repo_path = (query.get('path') or [PROJECT['path']])[0]
            project = next((p for p in self.session_projects.values() if p['path'] == repo_path), PROJECT)
            data = {'success': True, 'project': project, 'repos': [{
                'repo_root': repo_path, 'branch': 'fixture', 'clean': False,
                'total_files': 1, 'files': [{'path': 'src/fixture.py',
                'status': 'modified', 'additions': 1, 'deletions': 0}]}]}
        elif path == '/api/shell/workspaces':
            data = {'success': True, 'workspaces': []}
        elif path == '/api/shell/panes':
            data = {'success': True, 'columns': []}
        elif path == "/api/projects":
            registry = {p["id"]: p for p in [PROJECT, *self.session_projects.values()]}
            data = {"success": True, "data": list(registry.values())}
        elif path == "/api/auth/me":
            data = {"authenticated": True, "user": {"id": 1, "display_name": "Owner", "role": "owner"}}
        elif path == "/api/auth/sessions":
            data = {"success": True, "sessions": []}
        elif path == "/api/chat-pending-result":
            data = {"success": True, "pending": None}
        elif path == "/api/executing-jobs":
            data = {"success": True, "executing_jobs": []}
        elif path == "/api/ui-toasts":
            data = {"success": True, "toasts": []}
        else:
            data = {"success": True, "data": []}
        body = json.dumps(data)
        # Synchronous record; route callbacks run on the Playwright pump, so
        # no lock is needed and no sleep may stall here.
        self.requests.append(
            {
                "method": req.method,
                "path": path,
                "query": {k: v for k, v in query.items()},
                "req_bytes": len(post.encode("utf-8")),
                "resp_bytes": len(body.encode("utf-8")),
            }
        )
        route.fulfill(status=200, content_type="application/json", body=body)

    def _windowed(self, query):
        """Mirror the real route: limit=newest N, before_id=older page."""
        rows = self.rows

        def num(key):
            try:
                return int((query.get(key) or [""])[0])
            except (ValueError, TypeError):
                return None

        limit, before_id = num("limit"), num("before_id")
        if before_id is not None:
            msgs = [r for r in rows if r["id"] < before_id]
            msgs = msgs[-int(limit or PAGE_SIZE):]
            oldest = msgs[0]["id"] if msgs else before_id
            return {
                "success": True,
                "messages": msgs,
                "has_more": oldest > 1,
                "older_visible_count": oldest - 1,
            }
        if limit is not None:
            msgs = rows[-int(limit):]
            oldest = msgs[0]["id"] if msgs else 1
            return {
                "success": True,
                "messages": msgs,
                "has_more": oldest > 1,
                "older_visible_count": oldest - 1,
            }
        return {"success": True, "messages": list(rows)}


# Longtask observer only: no wrapping of setInterval/setTimeout/addEventListener
# (wrappers dropped native callback arguments and registration counts were
# misread as live-timer/leak evidence). Applies to every frame in the context.
LONGTASK_PROBE = """
window.__lt = window.__lt || [];
window.__ltUnsupported = false;
try {
  new PerformanceObserver(function (list) {
    var es = list.getEntries();
    for (var i = 0; i < es.length; i++) window.__lt.push(es[i].duration);
  }).observe({ entryTypes: ["longtask"] });
} catch (e) { window.__ltUnsupported = true; }
"""

# Transcript mutation probe, installed per frame once #chatMessages exists.
MUTATION_PROBE = """
window.__costMut = { added: 0, bubbles: 0 };
(function () {
  var box = document.getElementById('chatMessages');
  if (!box) return;
  window.__costMut.bubbles = box.querySelectorAll('.message').length;
  new MutationObserver(function (muts) {
    var n = 0, i;
    for (i = 0; i < muts.length; i++) n += muts[i].addedNodes.length;
    window.__costMut.added += n;
    window.__costMut.bubbles = box.querySelectorAll('.message').length;
  }).observe(box, { childList: true, subtree: true });
})();
"""

# Drive the production scroll-up handler: short transcripts do not overflow,
# so a physical scroll never fires — dispatch the scroll event the production
# listener is wired for, on the real element.
SCROLL_UP = """
(() => {
  const box = document.getElementById('chatMessages');
  if (!box) return 'no-box';
  try { box.scrollTop = 0; } catch (e) {}
  box.dispatchEvent(new Event('scroll'));
  return 'dispatched';
})()
"""


def fresh_context(browser, static_server, api):
    """One fresh context per run: guard + fake API + observer-only probe."""
    context = browser.new_context(viewport=dict(VIEWPORT))
    blocked = apply_request_guard(context, static_server)
    context.add_init_script(LONGTASK_PROBE)
    mascot = (ROOT / 'src/img/cuttle-mascot.png').read_bytes()
    context.route(f'{static_server}/img/**', lambda route: route.fulfill(
        status=200, content_type='image/png', body=mascot))
    context.route(f"{static_server}/api/**", api.handle)
    return context, blocked


def arm_diagnostics(page, errors, console_notes):
    """Zero-pageerror guard plus console context.

    pageerrors fail the run; console warnings/errors are context only (the
    product logs a warn on older-page fetch failure), attached to stall
    messages so a paging stall names its cause.
    """
    page.on("pageerror", lambda e: errors.append(str(e)))

    def on_console(msg):
        try:
            if msg.type in ("error", "warning"):
                console_notes.append(f"[{msg.type}] {msg.text[:300]}")
        except Exception:
            pass

    page.on("console", on_console)


def bubble_count(target):
    return target.evaluate(
        "() => document.querySelectorAll('#chatMessages .message').length"
    )


def frame_probe(target):
    return target.evaluate(
        "() => ({ bubbles: document.querySelectorAll('#chatMessages .message').length,"
        " added: (window.__costMut && window.__costMut.added) || 0,"
        " longtasks: (window.__lt || []).slice(),"
        " ltUnsupported: !!window.__ltUnsupported,"
        " unhighlighted: document.querySelectorAll("
        " '#chatMessages pre code:not(.hljs)').length })"
    )


def page_up_to_full(target, rows, api, console_notes, max_rounds=60):
    """Explicit full-history paging via the production scroll-up handler.

    Returns paging stats. Driver-driven by design: scroll rounds and request
    counts are driver/request cost, never render cost. Stops after 3
    consecutive no-progress rounds and reports completion honestly instead of
    burning the full round budget on timeouts.
    """
    def before_id_reqs():
        return len([r for r in api.requests
                    if r["path"].endswith("/messages") and "before_id" in r["query"]])

    before = before_id_reqs()
    rounds = 0
    stalled = 0
    complete = False
    target.wait_for_timeout(500)
    for _ in range(max_rounds):
        if bubble_count(target) >= rows:
            complete = True
            break
        prev = bubble_count(target)
        target.evaluate(SCROLL_UP)
        try:
            target.wait_for_function(
                "() => document.querySelectorAll('#chatMessages .message').length > %d" % prev,
                timeout=8000,
            )
            stalled = 0
            # Production ignores scroll events for 200ms after preserving its
            # prepend anchor. Drive the next scroll after that native cooldown;
            # this labeled driver pacing is not rendering latency.
            target.wait_for_timeout(250)
        except Exception:
            stalled += 1
            if stalled >= 3:
                break
            target.wait_for_timeout(500)
        rounds += 1
    return {
        "scroll_rounds": rounds,
        "before_id_requests": before_id_reqs() - before,
        "paging_complete": complete,
        "achieved": bubble_count(target),
        "console_tail": list(console_notes[-5:]),
    }


def by_endpoint(reqs):
    agg = {}
    for r in reqs:
        e = agg.setdefault(
            "%s %s" % (r["method"], r["path"]),
            {"count": 0, "req_bytes": 0, "resp_bytes": 0},
        )
        e["count"] += 1
        e["req_bytes"] += r["req_bytes"]
        e["resp_bytes"] += r["resp_bytes"]
    return agg


def steady_window(page, frames=()):
    """Steady-state observation on the Playwright clock.

    wait_for_timeout pumps route callbacks (raw time.sleep would starve the
    synchronous route handler). One light DOM poll per second keeps the cadence
    explicit; headless timer throttling is NOT claimed either way.
    """
    for _ in range(STEADY_S):
        page.wait_for_timeout(1000)
        page.evaluate("() => document.documentElement.clientWidth")
        for f in frames:
            try:
                f.evaluate("() => document.documentElement.clientWidth")
            except Exception:
                pass


def summarize_lts(samples):
    return {
        "count": len(samples),
        "total_ms": round(sum(samples), 1),
        "max_ms": round(max(samples), 1) if samples else 0,
    }


def environment():
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
            capture_output=True, text=True, encoding="utf-8", timeout=10,
        ).stdout.strip()
    except Exception:
        head = "unknown"
    return {
        "worktree": str(ROOT),
        "head": head or "unknown",
        "viewport": VIEWPORT,
        "runs_per_scenario": RUNS,
        "steady_window_s": STEADY_S,
        "production_page_size": PAGE_SIZE,
        "quiesce_ms": QUIESCE_MS,
        "opt_in": "CUTTLE_COST_PROFILE=1",
    }


def finalize(name, runs):
    inits = [r["initial_ready_ms"] for r in runs]
    doc = {
        "scenario": name,
        "environment": environment(),
        "method": (
            "fresh CostAPI + fresh browser context per run; same-origin static "
            "fixtures + fake API only (shared guard aborts CDN/live/DB); "
            "transcript/pending-strip readiness (newest %d rows) reported separately from "
            "explicit scroll-up paging (standalone: navigation start baseline; "
            "shell: navigation start baseline including iframe boot); "
            "%ds steady window on the Playwright clock; median/min/max over "
            "%d runs, no timing thresholds."
            % (PAGE_SIZE, STEADY_S, RUNS)
        ),
        "limitations": [
            "backend/DB cost not measured (deterministic fake API)",
            "timer/listener registration counts removed (were misread as leaks)",
            "no action forms in workload; highlight.js/fonts CDN blocked by "
            "guard so code renders unhighlighted (count recorded)",
            "zero longtasks is an observation, not a headless-throttling claim",
            "all shell panes share one project; counts are client requests, not backend cache behavior",
            "paging is driver-driven (dispatched scroll events); paging time "
            "is driver/request time, not render cost",
        ],
        "runs": runs,
        "initial_ready_ms": {
            "median": statistics.median(inits),
            "min": min(inits),
            "max": max(inits),
        },
    }
    record_result(doc)
    return doc


def record_result(doc):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    current[doc["scenario"]] = doc
    OUT.write_text(json.dumps(current, indent=2), encoding="utf-8")


def run_standalone(browser, static_server, rows, label):
    runs = []
    for _ in range(RUNS):
        api = CostAPI(build_history(rows))  # fresh per run: strictly per-run counters
        errors = []
        console_notes = []
        context, blocked = fresh_context(browser, static_server, api)
        try:
            page = context.new_page()
            arm_diagnostics(page, errors, console_notes)
            # Production boot path: session seeded once via ?chat= URL param.
            page.goto(f"{static_server}/chat_page.html?chat=42", wait_until="domcontentloaded")
            page.wait_for_function("typeof window.loadChatSession === 'function'", timeout=20000)
            page.evaluate(MUTATION_PROBE)
            page.wait_for_function(
                "() => document.querySelectorAll('#chatMessages .message').length >= %d"
                % min(rows, PAGE_SIZE),
                timeout=30000,
            )
            page.locator('#pendingChangesHost [data-pending-panel]').wait_for(state='visible')
            assert '1 file' in page.locator('#pendingChangesHost [data-pc="meta"]').inner_text()
            # Browser-clock transcript + pending-strip readiness (navigation = 0).
            initial_ready_ms = page.evaluate("() => Math.round(performance.now())")
            idx_initial = len(api.requests)
            if rows > PAGE_SIZE:
                paging = page_up_to_full(page, rows, api, console_notes)
            else:
                paging = {"scroll_rounds": 0, "before_id_requests": 0,
                          "paging_complete": True, "achieved": rows, "console_tail": []}
            page.wait_for_timeout(QUIESCE_MS)  # labeled settle, not render cost
            total_driver_ms = page.evaluate("() => Math.round(performance.now())")
            idx_paged = len(api.requests)
            assert paging["paging_complete"], \
                f"{label}: paging stalled at {paging['achieved']} of {rows}: {paging}"
            assert bubble_count(page) == rows, f"{label}: rendered {bubble_count(page)} of {rows}"
            text = page.evaluate("() => document.getElementById('chatMessages').textContent")
            assert "Plain status update 1" in text, f"{label}: expected content missing"
            ids = page.locator('#chatMessages .message[data-message-id]').evaluate_all(
                '(els) => els.map(e => Number(e.dataset.messageId)).sort((a,b) => a-b)')
            assert ids == list(range(1, rows + 1)), (label, ids)
            assert errors == [], f"{label}: page errors {errors}"
            assert any("cdn.jsdelivr.net" in u or "fonts.googleapis.com" in u for u in blocked), \
                f"{label}: guard did not block CDN traffic"
            steady_window(page)
            assert bubble_count(page) == rows
            assert errors == [], errors
            probes = [frame_probe(page)]
            lts = [d for p in probes for d in p["longtasks"]]
            runs.append({
                "initial_ready_ms": initial_ready_ms,
                "total_driver_ms": total_driver_ms,
                "paging": paging,
                "bubbles": sum(p["bubbles"] for p in probes),
                "expected_rows": rows,
                "transcript_mutations": sum(p["added"] for p in probes),
                "initial_window_requests": by_endpoint(api.requests[:idx_initial]),
                "paging_requests": by_endpoint(api.requests[idx_initial:idx_paged]),
                "steady_requests": by_endpoint(api.requests[idx_paged:]),
                "steady_window_s": STEADY_S,
                "chat_frame_longtasks": summarize_lts(lts),
                "longtask_supported": all(not p["ltUnsupported"] for p in probes),
                "code_blocks_unhighlighted": sum(p["unhighlighted"] for p in probes),
                "blocked_external": sorted(set(blocked)),
                "page_errors": list(errors),
                "console_tail": list(console_notes[-8:]),
            })
        finally:
            context.close()
    return finalize(label, runs)


def run_shell(browser, static_server, rows, panes, label):
    runs = []
    sids = [100 + i for i in range(panes)]
    for _ in range(RUNS):
        api = CostAPI(build_history(rows))  # fresh per run: strictly per-run counters
        errors = []
        console_notes = []
        context, blocked = fresh_context(browser, static_server, api)
        try:
            # Restore a persisted layout through the normal boot path. Never
            # call loadChatSession while an iframe is still being mounted.
            layout = {'version': 2, 'orientation': 'horizontal', 'root': {
                'type': 'group', 'id': 'cost-root', 'orientation': 'horizontal',
                'children': [{'type': 'leaf', 'id': f'cost-{sid}',
                              'page': f'/chat_page.html?chat={sid}',
                              'flex': '1 1 0%'} for sid in sids]}}
            context.add_init_script('localStorage.setItem("shell_split_layout", '
                                    + json.dumps(json.dumps(layout)) + ')')
            page = context.new_page()
            arm_diagnostics(page, errors, console_notes)
            page.goto(f"{static_server}/app_shell.html", wait_until="domcontentloaded")
            page.wait_for_function(
                "() => typeof window.getOpenPaneSessions === 'function'"
                " && window.getOpenPaneSessions().length === %d" % panes,
                timeout=30000)
            live = page.evaluate("() => window.getOpenPaneSessions().map(p => p.sessionId).sort()")
            assert live == sorted(str(s) for s in sids), (live, sids)
            frames = [f for f in page.frames if 'chat_page.html?chat=' in f.url]
            assert len(frames) == panes, (panes, [f.url for f in frames])
            for f in frames:
                f.wait_for_function(
                    "() => document.querySelectorAll('#chatMessages .message').length >= %d"
                    % min(rows, PAGE_SIZE), timeout=30000)
                f.locator('#pendingChangesHost [data-pending-panel]').wait_for(state='visible')
                assert '1 file' in f.locator('#pendingChangesHost [data-pc="meta"]').inner_text()
                f.evaluate(MUTATION_PROBE)
            # Both standalone and shell timings start at navigation, and include
            # boot + network + scheduling. They are readiness, not CPU render cost.
            initial_ready_ms = page.evaluate("() => Math.round(performance.now())")
            idx_initial = len(api.requests)
            counts = []
            pagings = []
            for f in frames:
                if rows > PAGE_SIZE:
                    pagings.append(page_up_to_full(f, rows, api, console_notes))
                else:
                    pagings.append({"scroll_rounds": 0, "before_id_requests": 0,
                                    "paging_complete": True, "achieved": rows,
                                    "console_tail": []})
                counts.append(bubble_count(f))
            page.wait_for_timeout(QUIESCE_MS)  # labeled settle, not render cost
            total_driver_ms = page.evaluate("() => Math.round(performance.now())")
            idx_paged = len(api.requests)
            assert all(c == rows for c in counts), f"{label}: panes rendered {counts}"
            assert errors == [], f"{label}: page errors {errors}"
            # Distinct sessions proven by distinct message-request paths.
            seen_sids = sorted({
                r["path"].split("/sessions/")[1].split("/")[0]
                for r in api.requests
                if "/sessions/" in r["path"] and r["path"].endswith("/messages")
            })
            assert seen_sids == sorted(str(s) for s in sids), \
                f"{label}: session paths {seen_sids} != {sids}"
            steady_window(page, frames)
            assert all(bubble_count(f) == rows for f in frames)
            assert errors == [], errors
            probes = [frame_probe(f) for f in frames]
            top = frame_probe(page)  # shell chrome only, NOT chat rendering
            lts = [d for p in probes for d in p["longtasks"]]
            runs.append({
                "initial_ready_ms": initial_ready_ms,
                "total_driver_ms": total_driver_ms,
                "paging_per_pane": pagings,
                "panes": panes,
                "pane_sessions": live,
                "pane_bubbles": counts,
                "bubbles": sum(p["bubbles"] for p in probes),
                "expected_rows": rows,
                "transcript_mutations": sum(p["added"] for p in probes),
                "initial_window_requests": by_endpoint(api.requests[:idx_initial]),
                "paging_requests": by_endpoint(api.requests[idx_initial:idx_paged]),
                "steady_requests": by_endpoint(api.requests[idx_paged:]),
                "steady_window_s": STEADY_S,
                "chat_frame_longtasks": summarize_lts(lts),
                "longtask_supported": all(not p["ltUnsupported"] for p in probes),
                "shell_chrome_longtasks": summarize_lts(top["longtasks"]),
                "code_blocks_unhighlighted": sum(p["unhighlighted"] for p in probes),
                "blocked_external": sorted(set(blocked)),
                "page_errors": list(errors),
                "console_tail": list(console_notes[-8:]),
            })
        finally:
            context.close()
    return finalize(label, runs)


def test_profile_standalone_short(browser, static_server):
    run_standalone(browser, static_server, 20, "standalone-short-20")


def test_profile_standalone_long(browser, static_server):
    run_standalone(browser, static_server, 400, "standalone-long-400")


def test_profile_shell_one_pane(browser, static_server):
    run_shell(browser, static_server, 20, 1, "shell-1pane-20")


def test_profile_shell_four_panes(browser, static_server):
    run_shell(browser, static_server, 20, 4, "shell-4pane-20")


def test_guard_blocks_external_allows_fixture(browser, static_server):
    """Guard integrity twin: same-origin works, CDN/live-localhost abort."""
    context, blocked = fresh_context(browser, static_server, CostAPI([]))
    try:
        page = context.new_page()
        page.goto(f"{static_server}/chat_page.html", wait_until="domcontentloaded")
        assert page.evaluate("async () => (await fetch('/chat_page.html')).status") == 200
        for url in (
            "https://cdn.jsdelivr.net/npm/highlight.js",
            "http://127.0.0.1:8080/api/health",
        ):
            outcome = page.evaluate(
                "async (u) => { try { await fetch(u); return 'loaded'; }"
                " catch (e) { return 'blocked'; } }",
                url,
            )
            assert outcome == "blocked", url
        assert any("cdn.jsdelivr.net" in u for u in blocked)
    finally:
        context.close()


def test_profile_database_window_and_page_meta(tmp_path):
    """Time real owned queries on fresh fixture databases, never user data."""
    import time
    from api.auth_db import AuthDatabase

    runs = []
    for run in range(RUNS):
        db = AuthDatabase(tmp_path / f'cost-{run}.db')
        uid = db.create_user('cost@example.invalid', 'Cost', 'fixture')
        assert uid
        sids = [db.create_chat_session(uid, f'Fixture {i}') for i in range(4)]
        conn = db._get_connection()
        conn.executemany(
            'INSERT INTO chat_messages(chat_session_id, role, content) VALUES (?, ?, ?)',
            [(sid, 'assistant' if i % 2 else 'user', f'row {i}')
             for sid in sids for i in range(400)])
        conn.commit()
        conn.close()
        samples = []
        for _ in range(25):
            started = time.perf_counter()
            for sid in sids:
                rows = db.get_messages(sid, limit=10)
                assert len(rows) == 10
                assert db.message_page_meta(sid, rows[0]['id']) == {
                    'has_more': True, 'older_visible_count': 390}
            samples.append((time.perf_counter() - started) * 1000)
        # Observe actual SQL emitted by the owner, then inspect those same
        # fixture-only SELECT plans. No handwritten replacement algorithm.
        traced = []
        get_connection = db._get_connection
        def observe_connection():
            conn = get_connection()
            conn.set_trace_callback(traced.append)
            return conn
        db._get_connection = observe_connection
        rows = db.get_messages(sids[0], limit=10)
        db.message_page_meta(sids[0], rows[0]['id'])
        db._get_connection = get_connection
        conn = get_connection()
        plans = []
        for query in traced:
            if query.lstrip().upper().startswith('SELECT'):
                plans.append({'sql': ' '.join(query.split()),
                              'plan': [row[3] for row in conn.execute(
                                  'EXPLAIN QUERY PLAN ' + query)]})
        conn.close()
        assert len(plans) == 3, plans
        runs.append({'median_ms': statistics.median(samples),
                     'min_ms': min(samples), 'max_ms': max(samples),
                     'four_session_reads_per_sample': 4,
                     'selects_per_session_window': len(plans), 'plans': plans})
    record_result({'scenario': 'database-4x400-window', 'runs': runs,
                   'method': 'Real AuthDatabase get_messages(limit=10) + message_page_meta; '
                             'three fresh SQLite databases, 25 samples of four sessions each.',
                   'limitations': 'Owner/database timings include connection and decode cost; '
                                  'exclude HTTP/auth/live-status/metadata enrichment and real user scale.'})


def test_profile_hidden_sync_and_foreground_resume(browser, static_server):
    """Synthetic visibility input; native scheduling, no OS-minimize claim."""
    runs = []
    for _ in range(RUNS):
        api = CostAPI(build_history(20))
        context, blocked = fresh_context(browser, static_server, api)
        context.add_init_script("window.__costHidden = false; "
                                "Object.defineProperty(document, 'hidden', {"
                                "configurable: true, get: () => window.__costHidden});")
        try:
            page = context.new_page()
            errors = []
            arm_diagnostics(page, errors, [])
            page.goto(f'{static_server}/chat_page.html?chat=42', wait_until='domcontentloaded')
            page.locator('#chatMessages .message').first.wait_for()
            page.wait_for_timeout(QUIESCE_MS)
            def history_requests():
                return sum(r['path'].endswith('/messages') for r in api.requests)
            page.evaluate("() => { window.__costHidden = true; "
                          "document.dispatchEvent(new Event('visibilitychange')); }")
            # Drain already dispatched visible requests before observing hidden.
            page.wait_for_timeout(QUIESCE_MS)
            start = history_requests()
            steady_window(page)
            hidden = history_requests() - start
            assert hidden == 0, hidden
            page.evaluate("() => { window.__costHidden = false; "
                          "window.__resumeAt = performance.now(); "
                          "document.dispatchEvent(new Event('visibilitychange')); }")
            # Response round trip is served by the route callback while pumping.
            for _ in range(40):
                if history_requests() > start:
                    break
                page.wait_for_timeout(50)
            assert history_requests() > start
            runs.append({'hidden_messages_requests': hidden,
                         'synthetic_hidden_window_s': STEADY_S,
                         'foreground_request_driver_observed_ms': page.evaluate(
                             '() => performance.now() - window.__resumeAt'),
                         'page_errors': errors})
            assert errors == []
        finally:
            context.close()
    record_result({'scenario': 'synthetic-hidden-sync', 'runs': runs,
                   'method': 'Three fresh pages, document.hidden getter controlled; '
                             'real visibilitychange handler, native timers and fixture HTTP.',
                   'limitations': 'Tests the policy input, not actual OS backgrounding. '
                                  'Resume timing includes up to 50 ms driver observation.'})


def test_profile_detached_stream_recovery(browser, static_server):
    """Native held stream, then one fixture parked result after actual detach."""
    from urllib.parse import parse_qs, urlparse
    from .test_chat_stream_reader import (
        StreamWorld, _open_stream, _send, _push_text, _session_frame,
        _frame, _js, _wait_typing, _wait_text, _assistant_count)

    runs = []
    for _ in range(RUNS):
        class RecoveryWorld(StreamWorld):
            def __init__(self):
                super().__init__()
                self.available = False
                self.running = False
                self.requests = []
            def handle(self, route):
                parsed = urlparse(route.request.url)
                if 'pending-result' in parsed.path:
                    data = {'success': True, 'pending': self.available, 'generating': self.running}
                    if self.available:
                        data['result'] = {'success': True, 'response': 'PROFILE-RECOVERED',
                                          'session_id': 42, 'type': 'chat'}
                    body = json.dumps(data)
                    self.requests.append({'path': parsed.path,
                                          'available': self.available,
                                          'response_bytes': len(body.encode())})
                    if parse_qs(parsed.query).get('consume') == ['1']:
                        self.available = False
                        self.running = False
                    route.fulfill(status=200, content_type='application/json', body=body)
                    return
                if parsed.path == '/api/chat-live-status':
                    route.fulfill(status=200, content_type='application/json', body=json.dumps(
                        {'success': True, 'active': self.running, 'generating': self.running,
                         'status': 'PROFILE-WORKING' if self.running else ''}))
                    return
                super().handle(route)
        world = RecoveryWorld()
        page, frame, errors = _open_stream(browser, static_server, world)
        try:
            _send(frame, 'profile stream recovery')
            world.running = True
            frame.locator('#stopButton').wait_for(state='visible')
            _push_text(frame, _session_frame() + _frame(
                {'type': 'status', 'message': 'PROFILE-WORKING'}))
            assert _wait_typing(frame, page, 'PROFILE-WORKING')
            # Preserve native 8s read ticks and 10s iframe hold (including
            # existing tick overshoot). Observe cancellation, don't force EOF.
            for _ in range(100):
                if _js(frame, '() => window.__streamState().cancelled'):
                    break
                page.wait_for_timeout(250)
            assert _js(frame, '() => window.__streamState().cancelled') == 1
            started = _js(frame, '() => performance.now()')
            world.available = True
            assert _wait_text(frame, page, 'PROFILE-RECOVERED', 12000)
            assert _assistant_count(frame) == 1
            assert errors == []
            runs.append({'recovery_driver_observed_ms':
                         _js(frame, '() => performance.now()') - started,
                         'stream': _js(frame, '() => window.__streamState()'),
                         'pending_requests': world.requests, 'page_errors': errors})
        finally:
            page.close()
    record_result({'scenario': 'native-detach-parked-recovery', 'runs': runs,
                   'method': 'Three fresh native stream pages; actual reader hold cancellation '
                             'then fixture pending result; history offers no assistant rescue.',
                   'limitations': 'Recovery latency includes polling and 250ms driver observation. '
                                  'Fixture API/executor; not vendor or backend latency.'})
