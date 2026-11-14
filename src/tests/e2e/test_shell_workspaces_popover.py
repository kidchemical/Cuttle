"""Workspace popover journeys (owner: src/web/js/shell/shell_workspaces.js).

Real shell assets over the static fixture server with a fake workspaces API
that follows the server's save semantics (id match → overwrite in place,
id without columns → rename only). Proves a saved workspace can be updated
and renamed without retyping its name, and that destructive row actions
need a confirming second click.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from .test_app_shell_layout import ShellWorld, _load_shell, _open, _pump_until
from .test_shared_diff_modal import (  # noqa: F401  (fixtures)
    browser,
    static_server,
)


class WorkspaceWorld(ShellWorld):
    def __init__(self):
        super().__init__()
        self.items = []
        self.posts = []
        self.deletes = []
        self._n = 0

    def _public(self, x):
        return dict(x, pane_count=len(x.get("columns") or []), summary=x.get("summary", "1 pane"))

    def handle(self, route):
        parsed = urlparse(route.request.url)
        path, method = parsed.path, route.request.method
        if path == "/api/shell/workspaces" and method == "POST":
            body = json.loads(route.request.post_data or "{}")
            self.posts.append(body)
            match = next((x for x in self.items if x["id"] == body.get("id")), None)
            if match is not None and "columns" not in body:
                match["name"] = body["name"]
            else:
                if match is None:
                    match = next((x for x in self.items
                                  if x["name"].lower() == body["name"].lower()), None)
                if match is None:
                    self._n += 1
                    match = {"id": f"ws{self._n}"}
                    self.items.insert(0, match)
                match.update(name=body["name"], columns=body.get("columns"), updated_at=1.0 + self._n)
            data = {"success": True, "workspace": self._public(match),
                    "workspaces": [self._public(x) for x in self.items]}
        elif path == "/api/shell/workspaces":
            data = {"success": True, "workspaces": [self._public(x) for x in self.items]}
        elif path.startswith("/api/shell/workspaces/") and method == "DELETE":
            wid = path.rsplit("/", 1)[-1]
            self.deletes.append(wid)
            self.items = [x for x in self.items if x["id"] != wid]
            data = {"success": True, "workspaces": [self._public(x) for x in self.items]}
        else:
            return super().handle(route)
        route.fulfill(status=200, content_type="application/json",
                      headers={"Cache-Control": "no-store"}, body=json.dumps(data))


def _open_popover(page):
    page.click("#nav-workspace")
    page.wait_for_selector("#workspacePopover.notifications-popover-open")


def test_save_update_rename_delete_without_retyping(browser, static_server):  # noqa: F811
    world = WorkspaceWorld()
    context, page, errors, _blocked = _open(browser, static_server, world)
    try:
        _load_shell(page, static_server)
        _open_popover(page)
        assert page.locator("#workspaceSaveBtn").is_disabled()
        assert page.locator("#workspaceUpdateBtn").is_hidden()
        assert page.inner_text("#workspaceCurrentName") == "Unsaved layout"

        page.fill("#workspaceNameInput", "Coding")
        page.click("#workspaceSaveBtn")
        _pump_until(page, lambda: len(world.posts) == 1, 5000, "first save")
        page.wait_for_function(
            "document.getElementById('workspaceCurrentName').textContent === 'Coding'")
        assert page.locator("#workspaceUpdateBtn").is_visible()
        assert page.input_value("#workspaceNameInput") == ""
        assert page.locator(".workspace-item.is-current").count() == 1

        # Update in place: no name typed, posts the saved id + its name + layout.
        page.click("#workspaceUpdateBtn")
        _pump_until(page, lambda: len(world.posts) == 2, 5000, "update")
        assert world.posts[1]["id"] == "ws1"
        assert world.posts[1]["name"] == "Coding"
        assert "columns" in world.posts[1]
        assert len(world.items) == 1

        # Typing an existing name says it will replace, not duplicate.
        page.fill("#workspaceNameInput", "coding")
        assert page.inner_text("#workspaceSaveBtn") == "Replace"
        assert page.locator("#workspaceSaveNote").is_visible()
        page.fill("#workspaceNameInput", "")

        # Rename only: id + name, no layout.
        row = page.locator('.workspace-item[data-workspace-id="ws1"]')
        row.locator("[data-workspace-rename]").click()
        rename = page.locator("[data-workspace-rename-input]")
        rename.fill("Deep work")
        rename.press("Enter")
        _pump_until(page, lambda: len(world.posts) == 3, 5000, "rename")
        assert world.posts[2] == {"id": "ws1", "name": "Deep work"}
        page.wait_for_function(
            "document.getElementById('workspaceCurrentName').textContent === 'Deep work'")

        # Delete needs a confirming second click.
        row.locator("[data-workspace-delete]").click()
        assert world.deletes == []
        assert row.locator("[data-workspace-delete]").inner_text() == "Confirm"
        row.locator("[data-workspace-delete]").click()
        _pump_until(page, lambda: world.deletes == ["ws1"], 5000, "delete")
        page.wait_for_function(
            "document.getElementById('workspaceCurrentName').textContent === 'Unsaved layout'")
        assert page.locator("#workspaceUpdateBtn").is_hidden()
        assert not errors, errors
    finally:
        context.close()
