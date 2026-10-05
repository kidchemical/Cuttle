"""Read Hermes progress from the exact invocation's persisted transcript."""

import json
import sqlite3
from contextlib import closing
from api.agent_harness.activity import ActivityEmitter, TextActivityLog, ToolActivityLog, text_preview


class HermesActivity:
    def __init__(self, db_path, status_queue, session_id=None):
        self.db_path = db_path
        self.session_id = session_id
        self.stderr = ""
        self.activity = ActivityEmitter(status_queue, agent_label="Hermes",
                                        record_text_previews=False, record_tool_previews=False)
        self.text = TextActivityLog("hermes")
        self.tools = ToolActivityLog("hermes", self.activity)
        self.first_id = 0
        self.seen = {}
        try:
            with closing(self._connect()) as conn:
                self.first_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0]
        except (sqlite3.Error, OSError):
            pass

    def _connect(self):
        # A polling adapter never creates or writes the vendor's database.
        return sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)

    def stderr_chunk(self, chunk):
        import re
        self.stderr = (self.stderr + chunk.decode("utf-8", errors="replace"))[-4000:]
        match = re.search(r"(?m)^\s*session_id:\s*(\S+)[ \t]*\r?\n", self.stderr)
        if match:
            self.session_id = match.group(1)

    def poll(self):
        if not self.session_id:
            return
        try:
            with closing(self._connect()) as conn:
                conn.row_factory = sqlite3.Row
                columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
                fields = ("id", "role", "content", "reasoning_content", "tool_calls", "tool_name", "tool_call_id", "is_error")
                select = ", ".join(field if field in columns else f"NULL AS {field}" for field in fields)
                rows = conn.execute(f"SELECT {select} FROM messages WHERE session_id = ? AND id > ? ORDER BY id DESC LIMIT 400",
                                    (self.session_id, self.first_id)).fetchall()
        except (sqlite3.Error, OSError):
            return
        self.seen = {row["id"]: self.seen[row["id"]] for row in rows if row["id"] in self.seen}
        for row in reversed(rows):
            record = dict(row)
            if self.seen.get(row["id"]) == record:
                continue
            self.seen[row["id"]] = record
            key = str(row["id"])
            if row["role"] == "assistant":
                for kind, field in (("thinking", "reasoning_content"), ("writing", "content")):
                    text = row[field]
                    if isinstance(text, str) and text.strip():
                        self.text.save(kind, text, key)
                        self.activity.emit(f"{kind}: {text_preview(text)}")
                try:
                    calls = json.loads(row["tool_calls"] or "[]")
                except (ValueError, TypeError):
                    calls = []
                for index, call in enumerate(calls if isinstance(calls, list) else []):
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") or {}
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            pass
                    self.tools.record(call.get("id") or f"{key}:{index}", str(fn.get("name") or call.get("name") or "tool"), args)
            elif row["role"] == "tool":
                failed = bool(row["is_error"])
                self.tools.record(row["tool_call_id"] or key, str(row["tool_name"] or "tool"),
                                  phase="failed" if failed else "completed", result=row["content"], failed=failed)
