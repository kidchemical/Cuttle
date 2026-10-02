"""Account-only Codex app-server RPCs; never resume or start a model turn."""
from contextlib import contextmanager
import queue
import subprocess
import threading
import time

from scripts.utilities.codex_app_server import _drain_stderr, _reader, _send
from scripts.utilities.codex_cli_tool import codex_executable


@contextmanager
def _account_rpc(timeout=30):
    exe = codex_executable()
    if not exe:
        raise RuntimeError("Codex CLI unavailable")
    proc = subprocess.Popen([exe, "app-server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    messages = queue.Queue()
    threading.Thread(target=_reader, args=(proc, messages), daemon=True).start()
    threading.Thread(target=_drain_stderr, args=(proc,), daemon=True).start()
    deadline = time.monotonic() + timeout
    request_id = 0

    def call(method, params=None):
        nonlocal request_id
        request_id += 1
        _send(proc, {"id": request_id, "method": method, "params": params or {}})
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Codex account request timed out")
            try:
                msg = messages.get(timeout=remaining)
            except queue.Empty:
                raise RuntimeError("Codex account request timed out") from None
            if msg is None:
                raise RuntimeError("Codex account connection closed")
            if msg.get("id") != request_id:
                continue
            if "error" in msg:
                # Provider errors may contain credentials or response bodies.
                raise RuntimeError("Codex account request failed; check login and CLI version")
            result = msg.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Unexpected Codex account response")
            return result

    try:
        call("initialize", {"clientInfo": {"name": "cuttle_usage", "version": "1.0"}})
        _send(proc, {"method": "initialized", "params": {}})
        yield call
    finally:
        if proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()  # Only the account helper process we spawned.
            proc.wait(timeout=2)


def read_codex_account_limits():
    with _account_rpc() as call:
        return call("account/rateLimits/read")


def consume_codex_reset(credit_id, idempotency_key, account_id=None):
    with _account_rpc() as call:
        if account_id:
            current = call("account/rateLimits/read")
            if current.get("accountId") != account_id:
                raise ValueError("Codex account changed. Request a new usage report.")
        return call("account/rateLimitResetCredit/consume", {
            "creditId": credit_id, "idempotencyKey": idempotency_key})
