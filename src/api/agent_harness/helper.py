"""Restricted background-text contract and lifecycle, separate from chat turns."""
from dataclasses import dataclass, field
import json
import queue
import os
from pathlib import Path
import subprocess
import threading
import time
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from uuid import uuid4

from core.agent_cli_env import agent_cli_env
from core.runtime_paths import runtime_state_path


@dataclass(frozen=True)
class HelperRequest:
    user: str
    system: str = ""
    model: str = ""
    effort: str | None = None
    timeout: float = 45
    json_object: bool = False
    auth: str = "subscription"
    cancel_event: object = None
    max_tokens: int = 200


@dataclass(frozen=True)
class HelperResult:
    text: str
    model: str = ""
    usage: dict = field(default_factory=dict)


class HelperCancelled(Exception):
    pass


def helper_env():
    env = agent_cli_env()
    # A background process is its own operation, never an extension of this chat.
    for key in list(env):
        if key.startswith(("CUTTLE_CHAT_", "CUTTLE_AGENT_", "CLAUDE_CODE_USE_")) or key in ("CLAUDECODE", "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
            env.pop(key, None)
    return env


class JsonProcess:
    """Bounded JSON lines, deadline and cancellation; only owns its spawned child."""
    def __init__(self, args, cwd, env, request):
        self.request = request
        self.deadline = time.monotonic() + request.timeout
        self.events = queue.Queue(maxsize=128)
        self.stopped = threading.Event()
        self.proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            total = 0
            # A pretty-printed document (``claude auth status --json``) spans lines;
            # collect from a line opening with "{" until the whole text parses.
            pending = b""
            while not self.stopped.is_set():
                raw = self.proc.stdout.readline(1048577)
                total += len(raw)
                if len(raw) > 1048576 or total > 4 * 1048576:
                    self._put(ValueError("CLI helper output exceeded its limit")); return
                if not raw: return
                try:
                    event = json.loads(raw)
                    pending = b""
                except ValueError:
                    if not pending and not raw.lstrip().startswith(b"{"):
                        continue
                    pending += raw
                    try:
                        event = json.loads(pending)
                    except ValueError:
                        continue
                    pending = b""
                if isinstance(event, dict): self._put(event)
        finally:
            self._put(None)

    def _put(self, event):
        while not self.stopped.is_set():
            try:
                self.events.put(event, timeout=.1); return
            except queue.Full:
                continue

    def send(self, event):
        self.proc.stdin.write((json.dumps(event) + "\n").encode())
        self.proc.stdin.flush()

    def next(self):
        while True:
            if self.request.cancel_event and self.request.cancel_event.is_set():
                raise HelperCancelled("Background helper cancelled")
            remaining = self.deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError("Background helper timed out")
            try:
                event = self.events.get(timeout=min(.1, remaining))
            except queue.Empty:
                continue
            if isinstance(event, Exception): raise event
            if event is None: raise RuntimeError("CLI helper ended before returning a result")
            return event

    def close(self):
        self.stopped.set()
        from api.chat_run_registry import kill_process_tree
        if self.proc.poll() is None:
            kill_process_tree(self.proc)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill(); self.proc.wait(timeout=10)
        self.reader.join(timeout=1)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream: stream.close()



@contextmanager
def helper_process(args, request, env=None):
    if request.cancel_event and request.cancel_event.is_set():
        raise HelperCancelled("Background helper cancelled")
    work = runtime_state_path("background_helpers", "runs")
    work.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="text-", dir=work) as cwd:
        from api.active_executions import register_execution, unregister_execution
        qid = "background-helper-" + uuid4().hex
        register_execution(qid, "Background text helper")
        process = None
        try:
            command = list(args)
            if "{helper_system_prompt}" in command:
                prompt_file = Path(cwd) / "system-prompt.txt"
                prompt_file.write_text((request.system or "Return only the requested text.") +
                    f"\nKeep the final answer within approximately {request.max_tokens} tokens.", encoding="utf-8")
                command = [str(prompt_file) if a == "{helper_system_prompt}" else a for a in command]
            if os.name == "nt":
                from api.agent_harness.win_cli import prefer_native_binary
                command[0] = prefer_native_binary(command[0], extra_candidates=[Path.home() / ".local" / "bin" / (Path(command[0]).stem + ".exe")])
                if Path(command[0]).suffix.lower() in (".cmd", ".bat", ".ps1"):
                    raise RuntimeError("Background helpers require a native CLI binary on Windows; install the native CLI or set its CLI_PATH")
            process = JsonProcess(command, cwd, env or helper_env(), request)
            yield process, cwd
        finally:
            try:
                if process: process.close()
            finally:
                unregister_execution(qid)
