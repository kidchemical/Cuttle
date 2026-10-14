"""Native (WebSocket) Realtime voice transport for clients without WebRTC.

The Android app loads the UI over LAN HTTP, so the page has no secure context
and cannot open a WebRTC peer connection or call ``getUserMedia``. GPT-Live
solved this with a native duplex audio bridge (`voice_live.service.native_connection`);
this module gives Realtime the same seam: the phone streams PCM to a Cuttle
WebSocket and Cuttle owns the OpenAI Realtime **WebSocket** session (server-side
API key), translating events to the shared ``session.*`` wire protocol.

The browser WebRTC path (`api.voice_realtime.create_session`) is unchanged.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from api import voice_usage
from api.experimental import is_enabled
from api.model_providers.catalog import get_provider
from api.model_providers.openai.adapter import client
from api.voice_live import store
from api.voice_live.delegation import run_request
from api.voice_realtime import load_settings, session_config

# Native PCM frames are 20 ms mono PCM16 at 24 kHz: 24000 * 2 * 0.02 = 960 bytes.
MAX_FRAME_BYTES = 48000
_RUN_RETENTION_SEC = 60

_runs: dict = {}
_lock = threading.RLock()


@contextmanager
def _realtime_client():
    provider = get_provider("openai")
    if not provider or not provider.configured():
        raise RuntimeError("Configure an OpenAI API key in Settings before using Realtime voice")
    import os
    base = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    if base.rstrip("/") != "https://api.openai.com/v1":
        raise RuntimeError("Realtime voice requires the official OpenAI base URL")
    with client(provider, 30) as sdk:
        yield sdk


@dataclass
class NativeRun:
    """One server-owned Realtime WebSocket session bound to a chat and a socket."""

    id: str
    user_id: int
    chat_id: int
    token: str
    prefs: dict
    prefix: str = ""
    chat_request: dict = field(default_factory=dict)
    socket: object = None
    connection: object = None
    ready: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)
    sending: threading.RLock = field(default_factory=threading.RLock)
    calls: set = field(default_factory=set)
    delegating: int = 0
    task_identity: dict = field(default_factory=dict)
    progress_at: float = 0.0
    last_activity: float = field(default_factory=time.monotonic)
    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str = ""
    closing: bool = False
    clean_close: bool = False
    input_seen: set = field(default_factory=set)
    output_seen: set = field(default_factory=set)

    @property
    def model(self):
        return self.prefs.get("model") or ""

    @property
    def transcription_model(self):
        return self.prefs.get("transcription_model") or "gpt-4o-mini-transcribe"

    def send(self, event):
        with self.sending:
            if self.socket is not None and not self.closed.is_set():
                self.socket.send(json.dumps(event))

    # ``run_request`` reuses the GPT-Live delegation bridge; it only reads these
    # attributes and calls append() for interim spoken progress.
    def append(self, kind, delegation_id, content):
        return

    def close(self):
        if self.closing or self.closed.is_set():
            return
        self.closing = True
        try:
            connection = self.connection
            if connection is not None:
                connection.close()
        except Exception:
            pass
        self.closed.set()


def _provider_send(run, event):
    """Serialize provider writes: the pump and delegation run on different threads."""
    with run.sending:
        connection = run.connection
        if connection is not None:
            connection.send(event)


def _record(run, responses, transcriptions):
    priced = voice_usage.record_realtime_usage(
        run.model, responses, transcriptions, session_id=run.chat_id,
        transcription_model=run.transcription_model)
    if priced.get("cost"):
        run.cost += float(priced["cost"])
    run.prompt_tokens += int(priced.get("prompt_tokens") or 0)
    run.completion_tokens += int(priced.get("completion_tokens") or 0)
    store.update(run.id, cost=run.cost, prompt_tokens=run.prompt_tokens,
                 completion_tokens=run.completion_tokens)
    return priced


def _handle_provider_event(run, raw):
    event = raw if isinstance(raw, dict) else (raw.model_dump() if hasattr(raw, "model_dump") else dict(raw))
    kind = event.get("type")
    event_id = str(event.get("event_id") or "")
    if kind == "session.updated":
        # Only after our session.update is acknowledged: audio must not start
        # against the connection's default (unconfigured) session.
        if not run.ready.is_set():
            run.ready.set()
            run.send({"type": "session.started", "event_id": event_id or uuid.uuid4().hex})
        return
    if kind == "response.output_audio.delta":
        run.last_activity = time.monotonic()
        run.send({"type": "session.output_audio.delta", "delta": event.get("delta"), "event_id": event_id})
        return
    if kind == "response.output_audio_transcript.delta":
        item_id = str(event.get("item_id") or "")
        run.output_seen.add(item_id)
        if event.get("delta"):
            _emit_transcript(run, "session.output_transcript.delta", event.get("delta"), event_id or uuid.uuid4().hex, item_id)
        return
    if kind == "response.output_audio_transcript.done":
        item_id = str(event.get("item_id") or "")
        if item_id not in run.output_seen and event.get("transcript"):
            _emit_transcript(run, "session.output_transcript.delta", event.get("transcript"), event_id or uuid.uuid4().hex, item_id)
        return
    if kind == "conversation.item.input_audio_transcription.delta":
        item_id = str(event.get("item_id") or "")
        run.input_seen.add(item_id)
        if event.get("delta"):
            _emit_transcript(run, "session.input_transcript.delta", event.get("delta"), event_id or uuid.uuid4().hex, item_id)
        return
    if kind == "conversation.item.input_audio_transcription.completed":
        item_id = str(event.get("item_id") or "")
        if item_id not in run.input_seen and event.get("transcript"):
            _emit_transcript(run, "session.input_transcript.delta", event.get("transcript"), event_id or uuid.uuid4().hex, item_id)
        usage = event.get("usage")
        if isinstance(usage, dict):
            _record(run, [], [{**usage, "_usage_id": item_id or event_id or uuid.uuid4().hex}])
        return
    if kind == "response.done":
        response = event.get("response") or {}
        usage = response.get("usage")
        if isinstance(usage, dict):
            _record(run, [{**usage, "_usage_id": response.get("id") or event_id or uuid.uuid4().hex}], [])
        for item in response.get("output") or []:
            if isinstance(item, dict) and item.get("type") == "function_call":
                threading.Thread(target=_delegate, args=(run, item), daemon=True).start()
        return
    if kind == "error":
        run.send({"type": "cuttle.error", "message": "Realtime voice reported an error; reconnect to retry."})
        return


def _emit_transcript(run, kind, delta, event_id, item_id=""):
    payload = {"type": kind, "delta": str(delta), "event_id": event_id}
    # The spoken item identity lets the client keep one bubble per utterance even
    # when a late input transcript lands between two output-transcript deltas.
    if item_id:
        payload["item_id"] = item_id
    if not store.event(run.id, payload):
        return  # replayed provider event
    run.last_activity = time.monotonic()
    run.send(payload)


def _delegate(run, item):
    call_id = str(item.get("call_id") or "")
    if not call_id or call_id in run.calls:
        return
    run.calls.add(call_id)
    connection = run.connection
    run.delegating += 1
    try:
        args = json.loads(item.get("arguments") or "{}")
        message = args.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 16000:
            result = {"error": "Invalid agent request; check the chat transcript."}
        elif not store.claim_delegation(run.id, call_id, {}):
            result = {"error": "This request was already handled; check the chat."}
        else:
            result = run_request(run, call_id, message.strip())
            store.finish_delegation(run.id, call_id, result)
    except Exception:
        result = {"error": "Agent handoff failed. Check the chat before retrying."}
    finally:
        # Drop any mic audio the phone buffered while the agent ran before we
        # start listening again, so the model answers the result, not stale speech.
        run.delegating -= 1
        if run.delegating <= 0:
            _provider_send(run, {"type": "input_audio_buffer.clear"})
    if connection is None or run.closed.is_set():
        return
    _provider_send(run, {"type": "conversation.item.create", "item": {
        "type": "function_call_output", "call_id": call_id, "output": json.dumps(result)}})
    _provider_send(run, {"type": "response.create"})


def _native_config():
    config = dict(session_config())
    # WebSocket carries raw PCM; WebRTC instead negotiates format through SDP.
    audio = dict(config.get("audio") or {})
    audio["input"] = {**audio.get("input", {}), "format": {"type": "audio/pcm", "rate": 24000}}
    audio["output"] = {**audio.get("output", {}), "format": {"type": "audio/pcm", "rate": 24000}}
    config["audio"] = audio
    return config


def _provider_loop(run, config):
    """Own the provider WebSocket for the whole call.

    A long agent delegation can outlive one provider socket; OpenAI may close an
    idle or over-limit Realtime session while the Cuttle agent is still working.
    Re-open it and keep the phone's audio socket (and call) alive instead of
    ending the conversation; only give up after repeated failures. Conversation
    context from before the drop is not replayed.
    """
    attempts = 0
    while not run.closed.is_set():
        try:
            with _realtime_client() as sdk:
                with sdk.realtime.connect(model=config["model"]) as connection:
                    run.connection = connection
                    _provider_send(run, {"type": "session.update",
                                         "session": {key: value for key, value in config.items() if key != "model"}})
                    run.error = ""
                    attempts = 0
                    for event in connection:
                        _handle_provider_event(run, event)
                        if run.closed.is_set():
                            break
        except Exception as exc:
            print(f"[VOICE] realtime provider error: {type(exc).__name__}: {exc}")
            if not run.ready.is_set():
                run.error = "Could not start the Realtime session; check key, model access and connection."
                break
        run.connection = None
        if run.closed.is_set():
            break
        attempts += 1
        if attempts > 2:
            run.error = "Realtime connection was lost; tap the orb to reconnect."
            break
        print(f"[VOICE] realtime provider closed; reconnecting (attempt {attempts})")
        time.sleep(0.4)
    run.ready.set()
    run.closed.set()


def _pump_client(run):
    while not run.closed.is_set():
        raw = run.socket.receive(timeout=2)
        if raw is None:
            if not run.socket.connected:
                break
            continue
        if len(raw) > 65536:
            raise ValueError("Audio frame too large.")
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "session.input_audio.append" and run.ready.is_set() and not run.closing:
            audio = base64.b64decode(event.get("audio", ""), validate=True)
            if len(audio) > MAX_FRAME_BYTES or len(audio) % 2:
                raise ValueError("Invalid PCM frame.")
            # While a Cuttle agent runs a delegated task the model is not
            # listening. Keep draining the phone socket (so its send queue never
            # backs up and trips LiveVoiceAudio's "network is too slow" close)
            # but don't forward mic audio to the busy provider session.
            if run.delegating <= 0:
                _provider_send(run, {"type": "input_audio_buffer.append", "audio": event["audio"]})
        elif kind == "session.close":
            run.clean_close = True
            print(f"[VOICE] realtime client requested close for {run.id}")
            break
        elif kind in {"session.input_audio.mute", "session.input_audio.unmute"}:
            # The native client gates capture locally; nothing to forward.
            continue


def _supervise(run):
    from api.auth_db import get_auth_db
    while not run.closed.wait(2):
        if not is_enabled("voice_realtime") or not get_auth_db().get_chat_session(run.chat_id, run.user_id):
            print(f"[VOICE] realtime supervise closing {run.id} (flag off or chat gone)")
            run.close()


def native_connection(voice_id, user_id, socket):
    run = _runs.get(voice_id)
    if not run or run.user_id != user_id or run.connection is not None:
        raise LookupError("Native voice session unavailable.")
    if not is_enabled("voice_realtime"):
        raise PermissionError("Realtime voice is disabled")
    run.socket = socket
    config = _native_config()
    try:
        threading.Thread(target=_provider_loop, args=(run, config), daemon=True).start()
        if not run.ready.wait(25) or run.error:
            raise RuntimeError(run.error or "Could not start the Realtime session.")
        store.update(voice_id, state="active")
        threading.Thread(target=_supervise, args=(run,), daemon=True).start()
        _pump_client(run)
        try:
            # Realtime has no provider `session.closed` event (the close is the
            # WebSocket itself); tell the phone explicitly so it can release the
            # microphone without waiting for its close timeout.
            run.send({"type": "session.closed"})
        except Exception:
            pass
        run.close()
        run.ready.wait(0.1)
    finally:
        run.closed.set()
        run.socket = None
        run.connection = None
        with _lock:
            _runs.pop(voice_id, None)
        print(f"[VOICE] realtime session {voice_id} ended clean_close={run.clean_close} error={run.error!r}")
        row = store.get(voice_id)
        if row and row["state"] != "closed":
            if run.clean_close:
                store.update(voice_id, state="closed", finalized=True)
            else:
                store.update(voice_id, state="disconnected", finalized=False,
                             error=run.error or "Native voice disconnected; final usage is unconfirmed.")


def start_native(*, user_id, chat_id, token, prefix="", chat_request=None):
    if not is_enabled("voice_realtime"):
        raise PermissionError("Realtime voice is disabled")
    prefs = load_settings()
    voice_id = uuid.uuid4().hex
    store.create(voice_id, user_id, chat_id, "native:" + voice_id,
                 {**prefs, "engine": "realtime", "native": True,
                  "prefix": prefix, "context": chat_request or {}})
    run = NativeRun(voice_id, user_id, chat_id, token, prefs,
                    prefix=prefix, chat_request=chat_request or {})
    with _lock:
        _runs[voice_id] = run
    store.update(voice_id, state="awaiting_audio")
    timer = threading.Timer(_RUN_RETENTION_SEC, lambda: run.close() if run.connection is None else None)
    timer.daemon = True
    timer.start()
    return snapshot(voice_id, user_id)


def snapshot(voice_id, user_id):
    row = store.get(voice_id, user_id)
    if not row:
        raise LookupError("Voice session not found.")
    if row["state"] == "active" and voice_id not in _runs:
        store.update(voice_id, state="disconnected", finalized=False)
        row = store.get(voice_id, user_id)
    data = row["data"]
    return {"id": row["id"], "chat_id": row["chat_id"], "state": row["state"],
            "model": data.get("model"), "voice": data.get("voice"), "native": True,
            "cost": data.get("cost", 0), "prompt_tokens": data.get("prompt_tokens", 0),
            "completion_tokens": data.get("completion_tokens", 0),
            "finalized": data.get("finalized", False),
            "error": data.get("error"), "transcripts": store.transcripts(voice_id)}


def close(voice_id, user_id):
    row = store.get(voice_id, user_id)
    if not row:
        raise LookupError("Voice session not found.")
    run = _runs.get(voice_id)
    if run:
        run.close()
        run.closed.wait(8)
    if row["state"] != "closed":
        store.update(voice_id, state="closed", finalized=True)
    return snapshot(voice_id, user_id)
