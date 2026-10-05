"""Documented dsh headless JSON event projection."""

import json
from api.agent_harness.activity import ActivityEmitter, TextActivityLog, ToolActivityLog, text_preview


class DeepSeekStream:
    def __init__(self, status_queue):
        self.activity = ActivityEmitter(status_queue, agent_label="DeepSeek Harness",
                                        record_text_previews=False, record_tool_previews=False)
        self.text = TextActivityLog("deepseek")
        self.tools = ToolActivityLog("deepseek", self.activity)
        self.final = None
        self.errors = []
        self.last_writing = None
        self.usage = {}
        self.sequence = 0

    def feed(self, raw):
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeError):
            return
        if not isinstance(event, dict):
            return
        typ = event.get("type")
        if typ in ("text", "thinking"):
            self.sequence += 1
            kind = "writing" if typ == "text" else "thinking"
            key = f"block-{self.sequence}"
            text = str(event.get("text") or "")
            if event.get("truncated"):
                text += "\n… *(truncated by DeepSeek Harness)*"
            self.text.save(kind, text, key)
            self.activity.emit(f"{kind}: {text_preview(text)}", force=True)
            if kind == "writing":
                self.last_writing = key
        elif typ == "final":
            self.final = str(event.get("text") or "")
            self.text.save("writing", self.final, self.last_writing or "final")
        elif typ == "tool_call":
            self.tools.record(event.get("callId"), str(event.get("tool") or "tool"), event.get("input"))
        elif typ == "tool_result":
            failed = event.get("status") == "error"
            self.tools.record(event.get("callId"), phase="failed" if failed else "completed",
                              result=event.get("result"), failed=failed)
        elif typ == "status":
            self.activity.emit(f"DeepSeek Harness: {event.get('phase') or 'working'}")
            if event.get("phase") == "step_end" and isinstance(event.get("usage"), dict):
                for source, target in (("inputTokens", "prompt_tokens"), ("outputTokens", "completion_tokens"),
                                       ("cacheReadTokens", "cache_read_tokens"), ("cacheWriteTokens", "cache_write_tokens")):
                    value = event["usage"].get(source)
                    if isinstance(value, (int, float)):
                        self.usage[target] = self.usage.get(target, 0) + int(value)
        elif typ == "error":
            message = str(event.get("message") or "DeepSeek Harness failed")
            self.errors.append(message)
            self.activity.emit(f"DeepSeek Harness error: {message}", force=True)

    def partial_output(self):
        return "\n\n".join(text for (kind, key), text in self.text.buffers.items() if kind == "writing")
