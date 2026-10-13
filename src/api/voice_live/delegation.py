"""Live task handoff through Cuttle's ordinary authenticated chat surface."""
import json
import re
import threading
import time

from api.auth_db import get_auth_db
from api.internal_http import _internal_api_base

_chat_locks = {}
_locks_lock = threading.Lock()


def chat_lock(chat_id):
    with _locks_lock:
        return _chat_locks.setdefault(chat_id, threading.Lock())


def speakable_chunks(text):
    from api.chat_tts import clean_text_for_speech
    text = clean_text_for_speech(text)
    # Byte BPE cannot produce more tokens than UTF-8 bytes. A conservative
    # 400-byte ceiling stays below 500 tokens without a tokenizer dependency.
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?\n])\s+", text):
        if len((current + " " + sentence).encode("utf-8")) > 400:
            if current:
                chunks.append(current)
            current = ""
        for word in sentence.split():
            if len((current + " " + word).encode("utf-8")) > 400:
                if current:
                    chunks.append(current)
                current = ""
                if len(word.encode("utf-8")) > 400:
                    # Raw code/URLs are not useful spoken results.
                    word = "[long technical detail is available in the chat]"
            current += (" " if current else "") + word
    if current:
        chunks.append(current)
    return chunks


def request_json(token, path, data, timeout=30):
    import requests
    from urllib.parse import urlparse
    base = _internal_api_base()
    if urlparse(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Voice delegation requires the local Cuttle API.")
    response = requests.post(base + path, json=data,
        headers={"Authorization": "Bearer " + token}, verify=False, timeout=timeout)
    body = response.json()
    if response.status_code >= 400 and response.status_code != 409:
        raise RuntimeError("Cuttle chat rejected the delegated request.")
    return response.status_code, body


def run_request(run, delegation_id, spoken):
    """Stable operation identity; stream replies and progress from the real chat route."""
    from api import chat_delivery
    sid = run.chat_id
    sid_key = "db_session_" + str(sid)
    message = run.prefix + spoken
    if not get_auth_db().get_chat_session(sid, run.user_id):
        return {"error": "The originating chat is no longer available."}
    request = {**run.chat_request, "session_id": sid_key, "message": message, "stream": True}
    if chat_delivery.is_busy(sid_key) or chat_delivery.is_busy(sid):
        turn = chat_delivery.current_turn(sid_key)
        before = get_auth_db().get_messages(sid, limit=1)
        before_id = before[-1]["id"] if before else 0
        code, steered = request_json(run.token, "/api/chat-steer", {"session_id": sid_key, "message": message})
        if steered.get("steered"):
            run.append("thinking", delegation_id, "The request was steered into the existing Cuttle turn; awaiting its reply.")
            while chat_delivery.is_busy(sid_key) and chat_delivery.current_turn(sid_key) == turn:
                time.sleep(0.5)
            if chat_delivery.is_turn_cancelled(sid_key) or chat_delivery.is_stale_turn(sid_key, turn):
                return {"error": "The steered turn was cancelled or superseded."}
            rows = get_auth_db().get_messages(sid, after_id=before_id)
            replies = [row for row in rows if row.get("role") == "assistant"]
            return {"response": replies[-1]["content"] if replies else "The reply is available in the chat."}
        # The durable shared queue keeps the request visible while waiting.
        import uuid
        queue_id = "voice:" + uuid.uuid5(uuid.NAMESPACE_URL, run.id + ":" + delegation_id).hex
        db = get_auth_db()
        db.mutate_followups(sid, run.user_id, "append", {"id": queue_id, "content": message, "paused": True})
        run.append("thinking", delegation_id, "The Cuttle agent is busy. This request is queued until its turn finishes.")
        while chat_delivery.is_busy(sid_key) or chat_delivery.is_busy(sid):
            if not db.get_chat_session(sid, run.user_id):
                return {"error": "The chat was removed."}
            time.sleep(0.5)
        claimed = db.mutate_followups(sid, run.user_id, "take_one", queue_id, claim_id=queue_id)
        if not claimed or not claimed.get("taken"):
            return {"error": "The queued request was removed; no work was started."}
    return stream_request(run, delegation_id, request)


def stream_request(run, delegation_id, data):
    import requests
    from urllib.parse import urlparse
    base = _internal_api_base()
    if urlparse(base).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Voice delegation requires the local Cuttle API.")
    result = {"error": "No Cuttle reply was delivered; check the chat before retrying."}
    # Never retry an ambiguous POST: the original turn may already be executing.
    with chat_lock(run.chat_id):
        response = requests.post(base + "/api/chat", json=data, stream=True, verify=False,
            headers={"Authorization": "Bearer " + run.token}, timeout=(10, 3100))
    with response:
        if response.status_code >= 400:
            return {"error": "Cuttle could not start the delegated turn; check the chat."}
        if "text/event-stream" not in response.headers.get("Content-Type", ""):
            return response.json()
        for line in response.iter_lines():
            if not line.startswith(b"data: "):
                continue
            event = json.loads(line[6:])
            if event.get("type") == "status":
                run.append("thinking", delegation_id, str(event.get("message") or "Working…"))
            if event.get("type") == "query_started":
                run.task_identity[delegation_id] = event.get("query_id")
            if event.get("type") == "response":
                result = event
            if event.get("type") == "done":
                break
    return result
