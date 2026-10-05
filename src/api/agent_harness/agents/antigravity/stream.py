"""Antigravity headless step updates and authoritative terminal result."""

import json
from api.agent_harness.activity import ActivityEmitter, TextActivityLog, ToolActivityLog, text_preview


class AntigravityStream:
    def __init__(self, status_queue, on_session):
        self.activity = ActivityEmitter(status_queue, agent_label="Antigravity",
                                        record_text_previews=False, record_tool_previews=False)
        self.text = TextActivityLog("antigravity")
        self.tools = ToolActivityLog("antigravity", self.activity)
        self.payload = None
        self.session_id = None
        self.on_session = on_session
        self.last_writing = None

    def feed(self, raw):
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if not isinstance(event, dict):
            return
        typ = event.get("event")
        if typ == "result":
            self.payload = event.get("result") or {}
            self.text.save("writing", str(self.payload.get("response") or ""), self.last_writing or "result")
        elif typ == "init":
            self.session_id = event.get("conversation_id")
            if self.session_id:
                self.on_session(self.session_id)
            self.activity.emit("Antigravity ready", force=True)
        elif typ == "step_update":
            step = event.get("step_update") or {}
            key = str(step.get("step_index"))
            state = step.get("state")
            kind = step.get("step_type")
            if kind == "agent_response":
                full = self.text.delta("writing", str(step.get("text_delta") or ""), key)
                if self.activity.emit(f"writing: {text_preview(full)}", force=state == "DONE") or state == "DONE":
                    self.text.save("writing", full, key)
                self.last_writing = key
            elif kind == "tool":
                info = step.get("tool_info") or {}
                failed = bool(info.get("error"))
                self.tools.record(key, str(step.get("tool_name") or info.get("name") or "tool"), info.get("parameters"),
                                  phase="failed" if failed else "completed" if state == "DONE" else "started",
                                  result=info.get("output") or info.get("error"), failed=failed)
            elif step.get("subagent_info"):
                self.tools.record(key, "subagent", step["subagent_info"], phase="completed" if state == "DONE" else "started")
            elif kind == "checkpoint":
                self.activity.emit(f"Antigravity checkpoint: {state or 'working'}")

    def partial_output(self):
        return "\n\n".join(text for (kind, key), text in self.text.buffers.items() if kind == "writing")
