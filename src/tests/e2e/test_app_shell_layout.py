"""E1 layout fences: nested split restore, real-UI close, reload, v1 migrate.

Real production shell assets (`app_shell.html` + `app_shell.js`) served from
the static fixture server with a fake same-origin API and a fresh browser
context per test (isolated localStorage). No live Flask, no daemon, no
vendors. The shell boots synchronously from localStorage; all waits are
predicate pumps on DOM/snapshot/storage/API state, never sleeps-as-proof.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .test_shared_diff_modal import (
    apply_request_guard,
    browser,  # noqa: F401  (existing fixture)
    static_server,  # noqa: F401  (existing fixture)
)

LAYOUT_KEY = "shell_split_layout"

V2_NESTED = {
    "version": 2,
    "root": {
        "type": "group", "id": "g-root", "orientation": "horizontal",
        "flex": "1 1 0%",
        "children": [
            {"type": "leaf", "id": "L1", "page": "/chat_page.html",
             "flex": "2 1 0%"},
            {"type": "group", "id": "G2", "orientation": "vertical",
             "flex": "1 1 0%", "children": [
                 {"type": "leaf", "id": "L2",
                  "page": "/chat_page.html?chat=7", "flex": "3 1 0%"},
                 {"type": "leaf", "id": "L3", "page": "/chat_page.html",
                  "flex": "1 2 0%"},
             ]},
        ],
    },
    "orientation": "horizontal",
}


def _live_flex(page):
    """Live inline flex by leaf/group id (mount applies node.flex verbatim)."""
    return page.evaluate(
        """() => {
            const out = {};
            document.querySelectorAll(
                ".split-column:not([data-split-discarded='1'])"
            ).forEach((c) => { out[c.dataset.leafId] = c.style.flex || ""; });
            document.querySelectorAll('.split-group').forEach((g) => {
                if (g.dataset.groupId) out[g.dataset.groupId] =
                    g.style.flex || "";
            });
            return out;
        }""")


def _saved_flex(page):
    root = json.loads(page.evaluate(
        f"localStorage.getItem('{LAYOUT_KEY}')"))["root"]
    out = {}

    def walk(n):
        if n.get("type") == "leaf":
            out[n["id"]] = n.get("flex", "")
        else:
            out[n.get("id")] = n.get("flex", "")
            for c in n.get("children", []):
                walk(c)

    walk(root)
    return out


def _electron_titlebar(page):
    """Simulate the Electron-only titlebar presentation (hidden
    #shellTitlebar shown only under body.is-electron). Mirrors existing
    shell browser tests; invokes no Electron APIs."""
    page.evaluate(
        "() => { document.body.classList.add('is-electron'); "
        "document.getElementById('shellTitlebar').hidden = false; }")


def test_norm_page_strips_only_new_flag():
    assert _norm_page("/chat_page.html?new=1") == "/chat_page.html"
    assert _norm_page("/chat_page.html?chat=7&new=1") == \
        "/chat_page.html?chat=7"
    assert _norm_page("/chat_page.html?session=abc&new=1") == \
        "/chat_page.html?session=abc"
    assert _norm_page("/chat_page.html?chat=7") == "/chat_page.html?chat=7"
    assert _norm_page("/chat_page.html?chat=7&session=8") == \
        "/chat_page.html?chat=7&session=8"

V1_FLAT = {
    "columns": [
        {"page": "/chat_page.html", "flex": ""},
        {"page": "/chat_page.html?chat=9", "flex": ""},
    ],
    "orientation": "horizontal",
}


class ShellWorld:
    """Fake same-origin API: auth/settings/workspaces/panes/history."""

    def __init__(self):
        self.panes_posts = []  # POST /api/shell/panes bodies

    def handle(self, route):
        parsed = urlparse(route.request.url)
        path, method = parsed.path, route.request.method
        if path == "/api/auth/me":
            data = {"authenticated": True, "user": {
                "id": 1, "display_name": "Owner", "role": "owner"}}
        elif path == "/api/settings/ui-layout":
            data = {"success": True, "data": {}}
        elif path == "/api/shell/workspaces":
            data = {"success": True, "workspaces": []}
        elif path == "/api/shell/panes":
            if method == "POST":
                self.panes_posts.append(
                    json.loads(route.request.post_data or "null"))
            data = {"success": True, "columns": []}
        elif path == "/api/projects":
            data = {"success": True, "data": []}
        elif path.endswith("/messages") and "/sessions/" in path:
            data = {"success": True, "messages": []}
        else:
            data = {"success": True, "data": []}
        route.fulfill(status=200, content_type="application/json",
                      headers={"Cache-Control": "no-store"},
                      body=json.dumps(data))


def _open(browser, static_server, world, seed=None):
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    blocked = apply_request_guard(context, static_server)
    # The static fixture serves src/web/ as root while the live server also
    # serves src/img/ at /img/. Serve the REAL mascot bytes so the rail
    # logo fallback path behaves as in production (a 404 would fire the
    # logo onerror handler instead). Registered after the guard, so the
    # guard still sees and allows same-origin /img traffic.
    mascot = (Path(__file__).resolve().parents[2]
              / "img" / "cuttle-mascot.png").read_bytes()
    context.route(f"{static_server}/img/**", lambda route: route.fulfill(
        status=200, content_type="image/png", body=mascot))
    if seed is not None:
        # Seed once: init scripts run on every navigation, so a reload must
        # keep the test-persisted layout, not the seed.
        context.add_init_script(
            f"if (!localStorage.getItem('{LAYOUT_KEY}')) "
            f"localStorage.setItem('{LAYOUT_KEY}', '{json.dumps(seed)}');")
    context.route(f"{static_server}/api/**", world.handle)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(
        str(error) + "\n" + str(getattr(error, "stack", ""))))
    return context, page, errors, blocked


def _load_shell(page, static_server):
    page.goto(f"{static_server}/app_shell.html",
              wait_until="domcontentloaded")
    page.wait_for_function("typeof window.snapshotLayoutTree === 'function'")


def _pump_until(page, predicate, timeout_ms, label):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting: {label}")
        page.wait_for_timeout(200)


def _snapshot(page):
    return page.evaluate(
        "window.snapshotLayoutTree(document.getElementById('splitContainer'))")


def _norm_page(page):
    """Drop the `new` flag: bare chat pages gain ?new=1 asynchronously after
    mount (app_shell.js:1383 intent), racing any snapshot — the stable
    contract is order/identity/session targeting, not the flag."""
    parts = urlparse(page or "")
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "new"]
    return urlunparse(parts._replace(query=urlencode(query)))


def _shape(node):
    """Structural projection: type/id/orientation/page/children only."""
    if node is None:
        return None
    if node.get("type") == "leaf":
        return ("leaf", node.get("id"), _norm_page(node.get("page")))
    return ("group", node.get("id"), node.get("orientation"),
            [_shape(c) for c in node.get("children", [])])


def _saved_root(page):
    return json.loads(page.evaluate(
        f"localStorage.getItem('{LAYOUT_KEY}')"))["root"]


def _await_new_post(world, page, before, expect_pages, label):
    """Await a panes POST published AFTER the snapshot count, then assert
    its columns. The latest pre-operation POST alone proves nothing about
    the operation under test."""
    _pump_until(page, lambda: len(world.panes_posts) > before, 20000, label)
    posted = world.panes_posts[-1]
    assert [_norm_page(c.get("page")) for c in posted["columns"]] == \
        expect_pages, posted
    return posted


def _col_count(page):
    # Discarded stubs stay in the DOM (display:none) until the deferred
    # purge; only live panes count (mirrors isSplitDiscardedEl).
    return page.locator(
        ".split-column:not([data-split-discarded='1'])"
        ":not([data-pane-closing='1'])").count()


def _assert_blocked_api_free(blocked):
    for url in blocked:
        assert "/api/" not in urlparse(url).path, f"API left origin: {url}"
        assert ":8080" not in url, f"live Flask touched: {url}"


def test_nested_split_restore_preserves_order_identity(browser, static_server):
    """Seeded nested v2 layout restores leaf order, ids, flex, orientation."""
    world = ShellWorld()
    context, page, errors, blocked = _open(
        browser, static_server, world, seed=V2_NESTED)
    try:
        _load_shell(page, static_server)
        _pump_until(page, lambda: _col_count(page) == 3, 20000, "3 panes")
        _pump_until(page, lambda: len(world.panes_posts) >= 1,
                    20000, "panes POST")
        assert _shape(_snapshot(page)) == (
            "group", "g-root", "horizontal", [
                ("leaf", "L1", "/chat_page.html"),
                ("group", "G2", "vertical", [
                    ("leaf", "L2", "/chat_page.html?chat=7"),
                    ("leaf", "L3", "/chat_page.html"),
                ]),
            ])
        # Seeded flex round-trips verbatim into live leaf/group styles and
        # the saved root. The root entry itself is live-only on the group
        # element with empty inline flex (mount does not re-apply it there).
        assert _live_flex(page) == {
            "L1": "2 1 0%", "G2": "1 1 0%", "L2": "3 1 0%",
            "L3": "1 2 0%", "g-root": "",
        }, _live_flex(page)
        # Saved root carries leaf/group flex; the root entry reads back ''
        # (mount does not re-apply seeded root flex to the saved shape).
        assert _saved_flex(page) == {
            "L1": "2 1 0%", "G2": "1 1 0%", "L2": "3 1 0%",
            "L3": "1 2 0%", "g-root": "",
        }
        # Saved layout + API persistence carry the same ids/order.
        assert _shape(_saved_root(page)) == _shape(_snapshot(page))
        posted = world.panes_posts[-1]
        assert [_norm_page(c.get("page")) for c in posted["columns"]] == [
            "/chat_page.html", "/chat_page.html?chat=7", "/chat_page.html"]
        assert posted["orientation"] == "horizontal"
        _assert_blocked_api_free(blocked)
        assert errors == [], errors
    finally:
        context.close()


def test_close_nested_pane_via_real_ui(browser, static_server):
    """Clicking a nested pane's close logo keeps the survivor + orientation."""
    world = ShellWorld()
    context, page, errors, blocked = _open(
        browser, static_server, world, seed=V2_NESTED)
    try:
        _load_shell(page, static_server)
        _pump_until(page, lambda: _col_count(page) == 3, 20000, "3 panes")
        # Close nested L2 through its own real close (×) rail logo,
        # targeted by leaf id (never by positional nth).
        posts_before = len(world.panes_posts)
        page.locator(
            ".split-column[data-leaf-id='L2'] .rail-logo-close").click()
        _pump_until(page, lambda: _col_count(page) == 2, 20000, "2 panes")
        _pump_until(
            page,
            lambda: _shape(_snapshot(page)) == (
                "group", "g-root", "horizontal", [
                    ("leaf", "L1", "/chat_page.html"),
                    ("leaf", "L3", "/chat_page.html"),
                ]),
            20000, "collapsed survivor shape")
        assert _shape(_saved_root(page)) == _shape(_snapshot(page))
        _await_new_post(world, page, posts_before,
                        ["/chat_page.html", "/chat_page.html"],
                        "panes POST after close")
        _assert_blocked_api_free(blocked)
        assert errors == [], errors
    finally:
        context.close()


def test_reload_preserves_space_layout_and_focus(browser, static_server):
    """Reload keeps the remaining layout, leaf ids, and the active space."""
    world = ShellWorld()
    context, page, errors, blocked = _open(
        browser, static_server, world, seed=V2_NESTED)
    try:
        _load_shell(page, static_server)
        _pump_until(page, lambda: _col_count(page) == 3, 20000, "3 panes")
        page.locator(
            ".split-column[data-leaf-id='L2'] .rail-logo-close").click()
        _pump_until(page, lambda: _col_count(page) == 2, 20000, "2 panes")
        first_space = page.evaluate("() => spacesState.active")
        _electron_titlebar(page)
        page.locator("#shellSpacesAdd").click()
        _pump_until(
            page,
            lambda: page.evaluate("() => spacesState.active") != first_space,
            20000, "new space activated")
        new_space = page.evaluate("() => spacesState.active")
        # Reload with the new space active: it stays active with its own
        # blank 1-pane layout.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "typeof window.snapshotLayoutTree === 'function'")
        _pump_until(
            page,
            lambda: page.evaluate("() => spacesState.active") == new_space,
            20000, "new space still active after reload")
        assert _col_count(page) == 1
        # Switch back through the real space tab: the first space restores
        # its remaining layout in LIVE frames, with no L2 resurrection.
        # (Titlebar presentation is DOM state, re-applied after reload.)
        _electron_titlebar(page)
        posts_before_switch = len(world.panes_posts)
        page.locator(
            f".shell-space-tab[data-space-id='{first_space}']").click()
        _pump_until(page, lambda: _col_count(page) == 2, 20000,
                    "2 live panes back in first space")
        live = _shape(_snapshot(page))
        assert live == (
            "group", "g-root", "horizontal", [
                ("leaf", "L1", "/chat_page.html"),
                ("leaf", "L3", "/chat_page.html"),
            ]), live
        assert "L2" not in json.dumps(live)
        assert page.evaluate("() => spacesState.active") == first_space
        # Switching published a new panes POST with the restored columns.
        _await_new_post(world, page, posts_before_switch,
                        ["/chat_page.html", "/chat_page.html"],
                        "panes POST after switch")
        # Reload with the original space active: live layout (not only the
        # stored root) survives and L2 stays gone.
        posts_before = len(world.panes_posts)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "typeof window.snapshotLayoutTree === 'function'")
        _pump_until(page, lambda: _col_count(page) == 2, 20000,
                    "2 live panes after reload")
        live = _shape(_snapshot(page))
        assert live == (
            "group", "g-root", "horizontal", [
                ("leaf", "L1", "/chat_page.html"),
                ("leaf", "L3", "/chat_page.html"),
            ]), live
        assert "L2" not in json.dumps(live)
        assert "L2" not in json.dumps(_saved_root(page))
        assert page.evaluate("() => spacesState.active") == first_space
        # Focus must index a live column (never a removed one) after
        # close + reloads; its exact value races iframe activity, and boot
        # reset (app_shell.js:1073) is source-evident, not fenced here.
        focused = page.evaluate("() => focusedColumnIdx")
        assert 0 <= focused < _col_count(page), focused
        assert _shape(_saved_root(page)) == _shape(_snapshot(page))
        # The reload itself published a new panes POST with the layout.
        _await_new_post(world, page, posts_before,
                        ["/chat_page.html", "/chat_page.html"],
                        "panes POST after reload")
        _assert_blocked_api_free(blocked)
        assert errors == [], errors
    finally:
        context.close()


def test_focus_follows_pointer_press_and_live_layout_reload(
        browser, static_server):
    """Real pointer presses on pane rails move focus (document capture,
    app_shell.js:1314; iframe activity also owns focus). No absolute values
    are asserted — only that focus tracks the pressed pane — and no reset
    or persistence is claimed. Reload re-proves the live 3-pane layout."""
    world = ShellWorld()
    context, page, errors, blocked = _open(
        browser, static_server, world, seed=V2_NESTED)
    try:
        _load_shell(page, static_server)
        _pump_until(page, lambda: _col_count(page) == 3, 20000, "3 panes")

        def _focused_leaf():
            return page.evaluate(
                """() => {
                    const cols = [...document.querySelectorAll(
                        ".split-column:not([data-split-discarded='1'])"
                        + ":not([data-pane-closing='1'])")];
                    const hit = cols.find((c) => Number(c.dataset.column)
                        === focusedColumnIdx);
                    return hit ? hit.dataset.leafId : null;
                }""")

        def _click_rail_bottom(leaf_id):
            rail = page.locator(
                f".split-column[data-leaf-id='{leaf_id}'] .icon-rail")
            box = rail.bounding_box()
            assert box and box["height"] > 60, box
            # Bottom of the rail, away from the logo controls.
            page.mouse.click(box["x"] + 5, box["y"] + box["height"] - 10)

        # Pointer capture tracks whichever pane is pressed, whatever the
        # boot/iframe focus was (live frames steal focus nondeterministically,
        # so no absolute initial value is asserted).
        _click_rail_bottom("L1")
        _pump_until(page, lambda: _focused_leaf() == "L1", 20000,
                    "focus on first pane")
        _click_rail_bottom("L3")
        _pump_until(page, lambda: _focused_leaf() == "L3", 20000,
                    "focus on third pane")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "typeof window.snapshotLayoutTree === 'function'")
        _pump_until(page, lambda: _col_count(page) == 3, 20000,
                    "3 panes after reload")
        # No reset assertion here by design: boot resets to 0
        # (app_shell.js:1073) but live iframe activity re-focuses after boot,
        # so any post-reload read races production behavior. The fence above
        # (0 → 2 on a real pointer press) plus the reset line in source is
        # the honest contract; focus persistence is never claimed.
        assert _shape(_snapshot(page))[3][0][1] == "L1"
        _assert_blocked_api_free(blocked)
        assert errors == [], errors
    finally:
        context.close()


def test_v1_flat_layout_migrates(browser, static_server):
    """Legacy flat shape boots through flatLayoutToTree into v2 + panes."""
    world = ShellWorld()
    context, page, errors, blocked = _open(
        browser, static_server, world, seed=V1_FLAT)
    try:
        _load_shell(page, static_server)
        _pump_until(page, lambda: _col_count(page) == 2, 20000, "2 panes")
        _pump_until(page, lambda: len(world.panes_posts) >= 1,
                    20000, "panes POST")
        snap = _snapshot(page)
        assert snap["type"] == "group" and snap["orientation"] == "horizontal"
        # NOTE: the v1-migration path preserves the bare page verbatim while
        # the v2 restore path canonicalizes bare pages to ?new=1 (observed
        # contract difference, recorded in the review; not asserted either way
        # as desirable here).
        assert [(c.get("type"), _norm_page(c.get("page")))
                for c in snap["children"]] == [
            ("leaf", "/chat_page.html"),
            ("leaf", "/chat_page.html?chat=9")]
        saved = json.loads(page.evaluate(
            f"localStorage.getItem('{LAYOUT_KEY}')"))
        assert saved["version"] == 2 and "root" in saved
        # The durable POST carries the same normalized pages.
        assert [_norm_page(c.get("page"))
                for c in world.panes_posts[-1]["columns"]] == [
            "/chat_page.html", "/chat_page.html?chat=9"]
        _assert_blocked_api_free(blocked)
        assert errors == [], errors
    finally:
        context.close()


def _park_state(page):
    return page.evaluate(
        """() => {
            const live = [...document.querySelectorAll(
                ".split-column:not([data-split-discarded='1'])")];
            const col = document.querySelector(
                ".split-column[data-leaf-id='L1']");
            const main = col && col.querySelector('.shell-main');
            const rail = col && col.querySelector('.icon-rail');
            const frame = col && col.querySelector(
                'iframe[data-park-probe="live"]');
            return {
                leaves: live.length,
                parked: document.querySelectorAll(
                    '.split-column.pane-collapsed').length,
                colWidth: col ? col.offsetWidth : -1,
                railVisible: !!(rail && rail.offsetWidth > 0),
                mainWidth: main ? main.offsetWidth : -1,
                frameAlive: !!(frame && frame.contentDocument),
                expandHandle: !!document.querySelector(
                    '#splitContainer > .split-resize-handle.pane-expand'),
            };
        }""")


def _saved_leaf_collapsed(page, leaf_id):
    root = _saved_root(page)
    found = {}

    def walk(node):
        if node.get("type") == "leaf":
            if node.get("id") == leaf_id:
                found["collapsed"] = node.get("collapsed", False)
            return
        for child in node.get("children", []):
            walk(child)

    walk(root)
    assert "collapsed" in found, f"leaf {leaf_id} missing from saved root"
    return found["collapsed"]


def test_collapsed_pane_parks_to_blade_and_restores(browser, static_server):
    """Parking (not closing) a pane: blade stays, divider restores it."""
    world = ShellWorld()
    context, page, errors, blocked = _open(
        browser, static_server, world, seed=V2_NESTED)
    try:
        _load_shell(page, static_server)
        _pump_until(page, lambda: _col_count(page) == 3, 20000, "3 panes")
        # Park L1 through the same entry the sub-100px drag release uses.
        # Tag its live iframe first: parking must not touch the frame.
        page.evaluate(
            "() => { const col = document.querySelector("
            "\" .split-column[data-leaf-id='L1']\");"
            " col.querySelector('iframe').dataset.parkProbe = 'live';"
            " window.setPaneCollapsed(col, true); }")
        _pump_until(page, lambda: _park_state(page)["parked"] == 1,
                    20000, "L1 parked")
        state = _park_state(page)
        assert state["leaves"] == 3, state       # parked, not closed
        assert state["railVisible"], state       # blade toolbar stays put
        assert state["mainWidth"] == 0, state    # content hidden in place
        assert state["frameAlive"], state        # live iframe untouched
        assert state["expandHandle"], state      # divider restore affordance
        assert 48 <= state["colWidth"] <= 64, state  # blade width only
        assert _saved_leaf_collapsed(page, "L1") is True
        # Park survives a full reload through the restore path.
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function(
            "typeof window.snapshotLayoutTree === 'function'")
        _pump_until(page, lambda: _col_count(page) == 3, 20000,
                    "3 panes after reload")
        _pump_until(page, lambda: _park_state(page)["parked"] == 1,
                    20000, "park restored after reload")
        # Restore through the real divider UI (release, not a drag).
        page.locator(
            "#splitContainer > .split-resize-handle.pane-expand").click()
        _pump_until(page, lambda: _park_state(page)["parked"] == 0,
                    20000, "L1 restored")
        restored = page.evaluate(
            """() => {
                const col = document.querySelector(
                    ".split-column[data-leaf-id='L1']");
                const main = col && col.querySelector('.shell-main');
                return {
                    leaves: [...document.querySelectorAll(
                        ".split-column:not([data-split-discarded='1'])")
                    ].length,
                    colWidth: col ? col.offsetWidth : -1,
                    mainOpacity: main
                        ? getComputedStyle(main).opacity : '?',
                    crumbs: document.querySelectorAll(
                        '.pane-will-collapse, .split-resize-handle.will-collapse'
                    ).length,
                    expandHandle: !!document.querySelector(
                        '.split-resize-handle.pane-expand'),
                };
            }""")
        assert restored["leaves"] == 3, restored
        # Pure click restores at equal shares, not the collapse floor.
        assert restored["colWidth"] > 300, restored
        assert not restored["expandHandle"], restored
        # No leftover drag preview: no dim, no red dash.
        assert restored["mainOpacity"] == "1", restored
        assert restored["crumbs"] == 0, restored
        _assert_blocked_api_free(blocked)
        assert errors == [], errors
    finally:
        context.close()


def _park_leaf(page, leaf_id):
    page.evaluate(
        "() => window.setPaneCollapsed(document.querySelector("
        f"\" .split-column[data-leaf-id='{leaf_id}']\"), true)")


def _leaf_width(page, leaf_id):
    return page.evaluate(
        "() => document.querySelector("
        f"\" .split-column[data-leaf-id='{leaf_id}']\").offsetWidth")


def _drag_expand_handle(page, dx):
    box = page.locator(
        "#splitContainer > .split-resize-handle.pane-expand").bounding_box()
    assert box is not None and box["width"] > 0
    x0, y0 = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x0, y0)
    page.mouse.down()
    page.mouse.move(x0 + dx, y0, steps=8)
    page.mouse.up()


def test_expand_divider_drag_sets_restored_width(browser, static_server):
    """Drag the expand divider to choose the width; nudges floor at 200."""
    world = ShellWorld()
    context, page, errors, blocked = _open(
        browser, static_server, world, seed=V2_NESTED)
    try:
        _load_shell(page, static_server)
        _pump_until(page, lambda: _col_count(page) == 3, 20000, "3 panes")
        _park_leaf(page, "L1")
        _pump_until(page, lambda: _park_state(page)["parked"] == 1,
                    20000, "L1 parked")
        # A real drag follows the pointer (blade 56px + 250px of drag).
        _drag_expand_handle(page, 250)
        _pump_until(page, lambda: _park_state(page)["parked"] == 0,
                    20000, "L1 restored by drag")
        dragged = _leaf_width(page, "L1")
        assert 270 <= dragged <= 340, dragged
        # A nudge past the press point settles at the collapse threshold.
        _park_leaf(page, "L1")
        _pump_until(page, lambda: _park_state(page)["parked"] == 1,
                    20000, "L1 parked again")
        _drag_expand_handle(page, 10)
        _pump_until(page, lambda: _park_state(page)["parked"] == 0,
                    20000, "L1 restored by nudge")
        nudged = _leaf_width(page, "L1")
        assert 185 <= nudged <= 215, nudged
        crumbs = page.evaluate(
            "() => document.querySelectorAll("
            "'.pane-will-collapse, "
            ".split-resize-handle.will-collapse').length")
        assert crumbs == 0, crumbs
        # A blade icon on the parked pane unparks it too.
        _park_leaf(page, "L1")
        _pump_until(page, lambda: _park_state(page)["parked"] == 1,
                    20000, "L1 parked once more")
        page.locator(
            ".split-column[data-leaf-id='L1'] "
            ".rail-item[data-page]").first.click()
        _pump_until(page, lambda: _park_state(page)["parked"] == 0,
                    20000, "L1 restored by blade icon")
        assert _leaf_width(page, "L1") > 300, _leaf_width(page, "L1")
        _assert_blocked_api_free(blocked)
        assert errors == [], errors
    finally:
        context.close()
