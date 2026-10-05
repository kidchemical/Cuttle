"""Claude Code NDJSON activity; final result stays separate from progress."""

import json

from api.agent_harness.activity import ActivityEmitter, TextActivityLog, ToolActivityLog, text_preview


class ClaudeStream:
    def __init__(self, status_queue, on_session):
        self.activity = ActivityEmitter(status_queue, agent_label="Claude Code",
                                        record_text_previews=False, record_tool_previews=False)
        self.text = TextActivityLog("claude")
        self.tools = ToolActivityLog("claude", self.activity)
        self.on_session = on_session
        self.session_id = None
        self.result = None
        self.messages = {}
        self.blocks = {}
        self.last_root_message = None

    def _block(self, scope, message_id, index, block, *, complete=False):
        key = f"{scope}:{message_id}:{index}"
        self.blocks[(scope, index)] = (key, block)
        kind = block.get("type")
        if kind in ("text", "thinking"):
            channel = "writing" if kind == "text" else "thinking"
            text = str(block.get("text" if kind == "text" else "thinking") or "")
            self.text.save(channel, text, key)
            if text:
                self.activity.emit(f"{channel}: {text_preview(text)}", force=complete)
        elif kind == "tool_use":
            self.tools.record(f"{scope}:{block.get('id')}", str(block.get("name") or "tool"), block.get("input"))

    def feed(self, raw):
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if not isinstance(obj, dict):
            return
        scope = str(obj.get("parent_tool_use_id") or "root")
        if scope == "root" and obj.get("session_id") and obj["session_id"] != self.session_id:
            self.session_id = str(obj["session_id"])
            self.on_session(self.session_id)
        typ = obj.get("type")
        if typ == "system":
            if obj.get("subtype") == "init":
                self.activity.emit(f"Claude Code ready (model: {obj.get('model') or 'default'})", force=True)
            elif obj.get("subtype") == "compact_boundary":
                self.activity.emit("Claude Code compacted context", force=True)
        elif typ == "result" and scope == "root":
            self.result = obj
            if not any(kind == "writing" for kind, key in self.text.buffers):
                self.text.save("writing", str(obj.get("result") or ""), "result")
        elif typ == "stream_event":
            event = obj.get("event") or {}
            et = event.get("type")
            index = event.get("index", 0)
            if et == "message_start":
                message = event.get("message") or {}
                self.messages[scope] = str(message.get("id") or f"message-{len(self.messages)}")
                if scope == "root":
                    self.last_root_message = self.messages[scope]
            elif et == "content_block_start":
                self._block(scope, self.messages.get(scope, "pending"), index, event.get("content_block") or {})
            elif et == "content_block_delta":
                delta = event.get("delta") or {}
                key, block = self.blocks.get((scope, index), (f"{scope}:pending:{index}", {}))
                channel = {"text_delta": "writing", "thinking_delta": "thinking"}.get(delta.get("type"))
                if channel:
                    field = "text" if channel == "writing" else "thinking"
                    full = self.text.delta(channel, str(delta.get(field) or ""), key)
                    if self.activity.emit(f"{channel}: {text_preview(full)}"):
                        self.text.save(channel, full, key)
                elif delta.get("type") == "input_json_delta":
                    block["partial_json"] = (block.get("partial_json", "") + str(delta.get("partial_json") or ""))[:24000]
            elif et == "content_block_stop":
                key, block = self.blocks.get((scope, index), (None, {}))
                kind = block.get("type")
                if kind in ("text", "thinking"):
                    channel = "writing" if kind == "text" else "thinking"
                    self.text.save(channel, item_id=key)
                elif kind == "tool_use":
                    try:
                        args = json.loads(block.get("partial_json") or "{}")
                    except ValueError:
                        args = block.get("partial_json")
                    self.tools.record(f"{scope}:{block.get('id')}", str(block.get("name") or "tool"), args)
        elif typ == "assistant":
            message = obj.get("message") or {}
            message_id = str(message.get("id") or self.messages.get(scope) or "pending")
            if scope == "root":
                self.last_root_message = message_id
            for index, block in enumerate(message.get("content") or []):
                if isinstance(block, dict):
                    self._block(scope, message_id, index, block, complete=True)
        elif typ == "user":
            message = obj.get("message") or {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    result = block.get("content") or ""
                    if isinstance(result, list):
                        result = "\n".join(str(b.get("text") or "") for b in result if isinstance(b, dict))
                    failed = bool(block.get("is_error"))
                    self.tools.record(f"{scope}:{block.get('tool_use_id')}", phase="failed" if failed else "completed",
                                      result=result, failed=failed)

    def partial_output(self):
        prefix = f"root:{self.last_root_message or 'pending'}:"
        return "\n".join(text for (kind, key), text in self.text.buffers.items()
                         if kind == "writing" and key.startswith(prefix)).strip()
