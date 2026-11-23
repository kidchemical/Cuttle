"""Execution node menu, remote badge and dialog, against fake routes."""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from .test_chat_action_card_render import CardWorld, _open_card_chat
from .test_shared_diff_modal import browser, static_server  # noqa: F401

HOST = {"id": "host", "label": "KCsMainFrame", "os": "Linux", "host": True, "state": "online", "reason": "",
        "default_cwd": "", "paths": {}}
LAPTOP = {"id": "kcslaptop", "label": "KCsLaptop", "os": "Windows", "host": False, "state": "online", "reason": "",
          "default_cwd": "C:\\Users\\me", "paths": {}}
TOWER = {"id": "kcstower", "label": "KCsTOWER", "os": "Windows", "host": False, "state": "unavailable",
         "reason": "offline", "default_cwd": "", "paths": {}}


class NodeWorld(CardWorld):
    def __init__(self, flag=True):
        super().__init__(["ok"])
        self.flag = flag
        self.binding = {"node_id": "host", "cwd": "", "node": HOST}
        self.calls = []

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path
        reply = lambda body, status=200: route.fulfill(  # noqa: E731
            status=status, content_type="application/json", body=json.dumps(body))
        if path == "/api/experimental/flags":
            return reply({"success": True, "flags": [{"id": "exec_nodes", "enabled": self.flag}]})
        if path == "/api/workers/exec/chats/42":
            if req.method == "PUT":
                body = json.loads(req.post_data or "{}")
                self.calls.append(body)
                node = {n["id"]: n for n in (HOST, LAPTOP, TOWER)}[body["node_id"]]
                self.binding = {"node_id": node["id"], "cwd": body.get("cwd", ""), "node": node}
                return reply({"success": True, "binding": self.binding})
            return reply({"success": True, "binding": self.binding, "nodes": [HOST, LAPTOP, TOWER]})
        return super().handle(route)


def test_chip_is_hidden_while_the_flag_is_off(browser, static_server):
    from playwright.sync_api import expect
    page, frame, errors = _open_card_chat(browser, static_server, NodeWorld(flag=False))
    try:
        page.wait_for_timeout(400)
        expect(frame.locator("#chatExecNodeSlot")).to_be_hidden()
        frame.get_by_role("button", name="Chat menu", exact=True).click()
        expect(frame.get_by_role("menuitem", name="Execution node…")).to_have_count(0)
        assert not errors, errors
    finally:
        page.context.close()


def test_pick_a_node_for_the_chat(browser, static_server):
    from playwright.sync_api import expect
    world = NodeWorld()
    page, frame, errors = _open_card_chat(browser, static_server, world)
    try:
        slot = frame.locator("#chatExecNodeSlot")
        chip = slot.locator("[data-exec-node-open]")
        menu_button = frame.get_by_role("button", name="Chat menu", exact=True)
        menu_item = frame.get_by_role("menuitem", name="Execution node…")
        menu_button.click()
        menu_item.click()
        expect(menu_item).to_be_hidden()
        form = frame.locator(".chat-supervision-dialog")
        expect(form).to_be_visible()
        expect(form.locator("h3")).to_have_text("Execution node of CH-000042")
        expect(form.locator("[name=nodeId]")).to_have_value("host")
        expect(slot).to_be_hidden()
        expect(form.locator("[data-exec-node-cwd]")).to_be_hidden()
        form.get_by_role("button", name="Execution node", exact=True).click()
        expect(frame.get_by_role("option", name="KCsTOWER — Windows · unavailable: offline")).to_be_visible()
        frame.get_by_role("option", name="KCsLaptop — Windows · online", exact=True).click()
        expect(form.locator("[data-exec-node-cwd]")).to_be_visible()
        expect(form.locator("[name=cwd]")).to_have_attribute("placeholder", "Default: mapped project, else C:\\Users\\me")
        expect(form.locator("[data-exec-node-hint]")).to_contain_text("The chat and its history stay here.")
        form.locator("[name=cwd]").fill("C:\\Users\\me\\Dev\\Cuttle")
        shot = os.environ.get("CUTTLE_E2E_SHOT_DIR")
        if shot:
            page.screenshot(path=os.path.join(shot, "exec_node_dialog.png"))
        form.locator("[type=submit]").click()
        expect(form).to_have_count(0)
        assert world.calls == [{"node_id": "kcslaptop", "cwd": "C:\\Users\\me\\Dev\\Cuttle"}]
        expect(chip).to_have_text("KCsLaptop")
        assert "is-active" in chip.get_attribute("class")
        if shot:
            page.screenshot(path=os.path.join(shot, "exec_node_chip.png"))

        page.set_viewport_size({"width": 320, "height": 700})
        frame.locator("#chatSessionTitleText").evaluate(
            "el => { el.textContent = 'A long chat title that competes for room with the execution node'; }"
        )
        assert chip.evaluate("el => getComputedStyle(el, '::before').content") == '"Node"'
        expect(chip).to_have_accessible_name("Agent runs on KCsLaptop — Windows (online). Click to change.")

        # Back to the host: the working directory is dropped with the node.
        chip.click()
        form.get_by_role("button", name="Execution node", exact=True).click()
        frame.get_by_role("option", name="KCsMainFrame — Linux · this Cuttle host", exact=True).click()
        form.locator("[type=submit]").click()
        expect(slot).to_be_hidden()
        assert world.calls[-1] == {"node_id": "host", "cwd": ""}
        menu_button.click()
        menu_item.click()
        expect(form.locator("[name=nodeId]")).to_have_value("host")
        assert not errors, errors
    finally:
        page.context.close()
