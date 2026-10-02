"""Shadow history-policy journeys over REAL HTTP (S2 journeys against
the frozen S1 dual-boot gate).

Real child Flask from B1 candidate bytes, real SQLite, real history
endpoints. No ``app.test_client``, no canned API responses. The ONLY fake
is the S1-armed deterministic executor: scenarios queued per execution
BEFORE the chat request (unique IDs, outcome names, hold, status texts);
request bodies stay production-shaped. Code-path proof is the child
execution counter plus fingerprints, never request flags.

S1 contract (matches ``api.dev_instance`` / ``cuttle_shadow_app.py``)::

    seed = dev_instance.prepare_snapshot(candidate_path)  # dict seed
    child, manifest = dev_instance.launch(
        seed, scenario="blocked", port=0)
    manifest: state/nonce/pid/port/codehash/anchors/origin/log
    child.stop()  (teardown touches only the owned handle)
    POST {origin}/__shadow/control  (X-Shadow-Nonce)
      {"queue": [{"id", "outcome", "response", "hold", "status": [texts],
                  "supervised_marker": "skip"|"coord"}], "release": [ids]}
    GET  {origin}/__shadow/control ->
      {"started": {id: n}, "completed": {id: n}, "statuses": {id: status},
       "blocked_attempts": [...full...], "blocked_counts": {kind: n},
       "canonical_ids": {id: realrowid}}

Outcome names: success | usefulfailure | emptyfailure | cancelled |
system | supervised. Unqueued executions are refused by the `blocked`
default (fail-closed); `blocked_attempts` must stay empty. Auth is real
HTTP register -> cookie -> sessions (one owner per child, fresh chat
session per test); the control channel never mints auth. Supervised
canonical rows are inserted by the child fake through the real child DB;
`canonical_ids[uid]` carries the actual row id asserted against history.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from api import dev_instance  # required S2 dependency: fail if missing

# S2 shadow journeys are verified on Linux only (loopback child +
# headless Chromium); pure B1 tests stay platform-neutral.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="S2 shadow journeys verified on Linux only")

CANDIDATE = Path(__file__).resolve().parents[2]

LANES = ("harness_sync", "harness_stream", "router_sync", "router_stream",
         "pipeline_sync", "pipeline_stream")

# Offline safety is owned by the child env whitelist in S1
# (dev_instance.build_child_env); no parent-side credential assert here.
_counter = itertools.count()


# ---------------------------------------------------------------------------
# Real-HTTP client (urllib + cookie jar, repo convention; env proxies
# disabled so loopback can never escape through a proxy).
# ---------------------------------------------------------------------------

class ShadowHttp:
    def __init__(self, origin):
        import http.cookiejar

        self.origin = origin.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(self.jar))

    def _call(self, method, path, payload=None, timeout=30):
        import urllib.error

        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.origin + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            # Real non-2xx (e.g. 409 busy): decode the actual body/status,
            # never a canned response.
            return exc.code, json.loads(exc.read().decode())

    def post(self, path, payload, timeout=30):
        return self._call("POST", path, payload, timeout)

    def get(self, path, timeout=30):
        return self._call("GET", path, None, timeout)


def _owner_name():
    # Register usernames allow a-z0-9_ (no hyphens); keep it short.
    return f"s2owner_{os.getpid()}_{next(_counter)}"[:32]


def _register_owner(http, username):
    status, body = http.post("/api/auth/register", {
        "username": username,
        "password": "shadow-pass-123",
        "display_name": "S2 Owner",
    })
    assert status == 200, body
    token = next(
        (c.value for c in http.jar if c.name == "session_token"), None)
    assert token, "register must set session_token cookie"
    return token


def _new_chat(http, tag):
    status, body = http.post("/api/auth/sessions",
                             {"session_name": f"s2 {tag}"})
    assert status == 200, body
    assert body.get("success") is True, body
    assert body.get("session_id"), body
    return body["session_id"]


def _history(http, sid):
    status, body = http.get(f"/api/auth/sessions/{sid}/messages")
    assert status == 200, body
    assert body.get("success") is True, body
    return body["messages"]


def _control(http, nonce, payload):
    req = urllib.request.Request(
        http.origin + "/__shadow/control",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "X-Shadow-Nonce": nonce},
        method="POST")
    with http.opener.open(req, timeout=30) as resp:
        assert resp.status == 200, resp.status
        return json.loads(resp.read().decode())


def _control_state(http, nonce):
    req = urllib.request.Request(
        http.origin + "/__shadow/control",
        headers={"X-Shadow-Nonce": nonce},
        method="GET")
    with http.opener.open(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Shadow child lifecycle: one child per module, one owner, fresh chat each
# test. Stop is guaranteed even when setup assertions fail.
# ---------------------------------------------------------------------------

# The two B1 production files whose loaded bytes the child must prove
# (manifest anchor sha must equal the snapshotted working-tree sha).
_B1_PROOF_FILES = ("src/api/chat_turn_workflow.py",
                   "src/api/web_chat_api.py")


@pytest.fixture(scope="module")
def shadow():
    seed = dev_instance.prepare_snapshot(CANDIDATE)
    assert seed.get("app_dir") and seed.get("codehash"), seed.keys()
    child, manifest = dev_instance.launch(
        seed, scenario="blocked", port=0)
    try:
        for key in ("origin", "nonce", "codehash", "port"):
            assert manifest.get(key), manifest.keys()
        assert manifest["codehash"] == seed["codehash"], (
            "child must run the snapshotted bytes")
        assert int(manifest["port"]) != 8080, "never the live port"
        seed_anchors = seed.get("anchor_hashes") or {}
        anchors = manifest.get("anchors") or {}
        for rel in _B1_PROOF_FILES:
            assert rel in seed_anchors, seed_anchors.keys()
            assert (anchors.get(rel) or {}).get("sha") == seed_anchors[rel], (
                f"child did not load candidate bytes: {rel}")
        print("S2 shadow proof: "
              f"codehash={seed['codehash']} "
              + " ".join(
                  f"{rel.rsplit('/', 1)[-1]}={seed_anchors[rel][:16]}"
                  for rel in _B1_PROOF_FILES)
              + f" files={seed.get('files')}"
              f" origin={manifest.get('origin')}"
              f" pid={manifest.get('pid')}"
              f" port={manifest.get('port')}"
              f" handshake={manifest.get('handshake')}", flush=True)
        http = ShadowHttp(manifest["origin"])
        _register_owner(http, _owner_name())
    except BaseException:
        child.stop()
        raise
    try:
        yield child, manifest, http
    finally:
        try:
            end = _control_state(http, manifest["nonce"])
            started = sum(end.get("started", {}).values())
            completed = sum(end.get("completed", {}).values())
            counts = end.get("blocked_counts", {})
            blocked = end["blocked_attempts"]
            print(f"S2 shadow totals: started={started} "
                  f"completed={completed} blocked={len(blocked)} "
                  f"blocked_counts={counts} "
                  f"blocked_attempts={blocked}", flush=True)
        except Exception as exc:
            print(f"S2 shadow totals unavailable: {exc!r}", flush=True)
        child.stop()


@pytest.fixture()
def live(shadow):
    _child, manifest, http = shadow
    nonce = manifest["nonce"]
    sid = _new_chat(http, f"case-{next(_counter)}")
    return manifest, http, nonce, sid


def _queue(http, nonce, outcome, response, **kw):
    uid = f"s2-{next(_counter)}"
    entry = {"id": uid, "outcome": outcome, "response": response,
             "hold": False, "status": []}
    entry.update(kw)
    body = _control(http, nonce, {"queue": [entry], "release": []})
    assert body.get("success") is True, body
    return uid


def _release(http, nonce, *uids):
    body = _control(http, nonce, {"queue": [], "release": list(uids)})
    assert body.get("success") is True, body


def _lane_message(lane, tag):
    if lane.startswith("harness"):
        return f"/cursor s2 probe {tag}"
    if lane.startswith("router"):
        # Real parser contract: `/route <agent> <model> <prompt>` runs
        # integration.execute_explicit_target (fake-held in the child).
        return f"/route cursor auto s2 probe {tag}"
    return f"plain s2 probe {tag}"


def _chat(http, lane, sid, tag, timeout=30):
    payload = {"message": _lane_message(lane, tag), "session_id": sid}
    if lane.endswith("_sync"):
        payload["stream"] = False
    if lane.startswith("pipeline"):
        payload["sticky_agent"] = "none"
    return http.post("/api/chat", payload, timeout=timeout)


def _pump_until(fn, timeout_ms, label):
    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        if fn():
            return
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting: {label}")
        time.sleep(0.2)


class _SseDrain(threading.Thread):
    """Incremental SSE drain on a daemon thread: frame-by-frame reads with
    a bound socket timeout, so the driver observes status bytes BEFORE
    release/terminal and asserts liveness via join()."""

    def __init__(self, http, lane, sid, tag):
        super().__init__(daemon=True)
        self.http = http
        self.lane = lane
        self.sid = sid
        self.tag = tag
        self.frames = []
        self.error = None

    def run(self):
        try:
            payload = {"message": _lane_message(self.lane, self.tag),
                       "session_id": self.sid}
            if self.lane.startswith("pipeline"):
                payload["sticky_agent"] = "none"
            req = urllib.request.Request(
                self.http.origin + "/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST")
            with self.http.opener.open(req, timeout=10) as resp:
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    self.frames.append(line.decode())
        except Exception as exc:  # recorded; the test asserts on it
            self.error = exc

    def types(self):
        out = []
        for chunk in self.frames:
            for line in chunk.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    out.append(json.loads(line[len("data: "):]).get("type"))
                except ValueError:
                    out.append("<bad-json>")
        return out

    def saw_status(self):
        return "status" in self.types()

    def status_texts(self):
        out = []
        for chunk in self.frames:
            for line in chunk.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    payload = json.loads(line[len("data: "):])
                except ValueError:
                    continue
                if payload.get("type") == "status" and "message" in payload:
                    out.append(payload["message"])
        return out


def _role_contents(rows):
    return [(m["role"], m["content"]) for m in rows]


def _assert_exact_history(rows, want, label):
    actual = _role_contents(rows)
    assert actual == want, (
        f"exact-history mismatch for {label}: actual={actual} want={want}")


# ---------------------------------------------------------------------------
# Curated matrix: all six lanes x key policy outcomes (outcome payloads
# only — lifecycle lives in the dedicated tests below).
# ---------------------------------------------------------------------------

CASES = ("success", "usefulfailure", "emptyfailure", "cancelled", "system",
         "supervised_skip", "supervised_coord")
MATRIX = ([(lane, case) for lane in LANES
           for case in ("success", "usefulfailure", "cancelled",
                        "supervised_skip")]
          + [("harness_sync", "emptyfailure"),
             ("harness_sync", "system"),
             ("harness_sync", "supervised_coord"),
             ("pipeline_stream", "emptyfailure"),
             ("pipeline_stream", "system"),
             ("pipeline_stream", "supervised_coord")])


def _outcome_spec(case):
    """(outcome name, response text, extra scenario keys)."""
    return {
        "success": ("success", "shadow reply", {}),
        "usefulfailure": ("usefulfailure", "failed with text", {}),
        "emptyfailure": ("emptyfailure", "", {}),
        "cancelled": ("cancelled", "[CANCELLED] stopped by user", {}),
        "system": ("system", "background note", {}),
        "supervised_skip": ("supervised", "supervised done",
                             {"supervised_marker": "skip"}),
        "supervised_coord": ("supervised", "supervised done",
                             {"supervised_marker": "coord"}),
    }[case]


def _expected_assistants(lane, case):
    """B1 unified policy over real HTTP (candidate behavior). S1 patches
    execute_decision, so the real pipeline outer returns the shadow reply
    on ALL six lanes (abstain-fallback is covered by existing suites)."""
    return {
        "success": ["shadow reply"],
        "usefulfailure": ["failed with text"],
        "emptyfailure": [],
        "cancelled": [],
        "system": [],
        # Canonical row content is the scenario response (child inserts it
        # through the real DB; the marker itself is saver-skipped).
        "supervised_skip": ["supervised done"],
        "supervised_coord": ["supervised done"],
    }[case]


def _run_history_policy_case(live, lane, case):
    """Shared body (also driven by the temp pre-B1 control artifact)."""
    manifest, http, nonce, sid = live
    tag = f"{lane}-{case}-{next(_counter)}"
    name, response, extra = _outcome_spec(case)
    stream_status = f"S2 executor status {tag}" if lane.endswith(
        "_stream") else None
    uid = _queue(http, nonce, name, response,
                 status=[stream_status] if stream_status else [], **extra)

    if lane.endswith("_sync"):
        status, body = _chat(http, lane, sid, tag)
        assert status == 200, body
        assert "response" in body
    else:
        drain = _SseDrain(http, lane, sid, tag)
        drain.start()
        drain.join(timeout=60)
        assert not drain.is_alive(), "SSE drain thread stuck"
        assert drain.error is None, drain.error
        kinds = drain.types()
        assert kinds[0] == "session"
        assert kinds[-1] == "done"
        if lane.startswith("pipeline"):
            # Characterized gap, D1/D2 followup (preexists B1, no fix
            # here): the pipeline compat entry re-submits unclaimed
            # (process_message_with_bot -> coordinator status_queue=None
            # arm), so the fake's custom status chunk cannot reach SSE.
            # The stream must still carry real session/route-status/done
            # framing; fake execution is proven by the started/completed
            # counters and the exact persisted rows below.
            assert "[route] Routing to top-level agent..." in (
                drain.status_texts()), (
                "pipeline route-phase status must frame the stream")
            assert stream_status not in drain.status_texts(), (
                "custom executor chunk must stay absent on pipeline "
                "SSE until D1/D2 rewires the compat queue")
        else:
            assert stream_status in drain.status_texts(), (
                "exact custom executor chunk must precede terminal")

    state = _control_state(http, nonce)
    assert state["started"].get(uid, 0) == 1, state

    message = _lane_message(lane, tag)
    rows = _history(http, sid)
    assistants = [m for m in rows if m["role"] == "assistant"]
    users = [m for m in rows if m["role"] == "user"]
    assert len(rows) == len(users) + len(assistants)
    expected = _expected_assistants(lane, case)
    want = [("user", message)] + [("assistant", text) for text in expected]
    _assert_exact_history(rows, want, f"{lane}/{case}")
    if case.startswith("supervised"):
        assert [m.get("id") for m in assistants] == [
            state["canonical_ids"][uid]]

    fuid = _queue(http, nonce, "success", "followup ok")
    followup_message = f"/cursor followup {tag}"
    status, body = http.post("/api/chat", {
        "message": followup_message, "session_id": sid,
        "stream": False})
    assert status == 200, body
    assert body.get("response") == "followup ok"
    rows = _history(http, sid)
    _assert_exact_history(
        rows,
        want + [("user", followup_message), ("assistant", "followup ok")],
        f"{lane}/{case} followup")

    # Full-journey guard check: zero blocked attempts AFTER the history
    # reads (history hydration itself must not touch vendor config) and
    # AFTER followup completion with its history validated.
    state = _control_state(http, nonce)
    assert state["started"].get(uid, 0) == 1, state
    assert state["started"].get(fuid, 0) == 1, state
    assert state["blocked_attempts"] == [], state
    assert all(v == 0 for v in state["blocked_counts"].values()), state


@pytest.mark.parametrize("lane,case", MATRIX,
                         ids=[f"{lane}-{case}" for lane, case in MATRIX])
def test_shadow_history_policy(live, lane, case):
    _run_history_policy_case(live, lane, case)


# ---------------------------------------------------------------------------
# Lifecycle over REAL HTTP: held requests in worker threads, REAL cancel
# API as the driver. No virtual turn flags anywhere.
# ---------------------------------------------------------------------------

def _post_thread(http, lane, sid, tag, sink):
    # The SSE drain is published BEFORE start so the driver polls the
    # incremental stream while the fake is held.
    drain = None
    if lane.endswith("_stream"):
        drain = _SseDrain(http, lane, sid, tag)
        sink["drain"] = drain

    def run():
        try:
            if lane.endswith("_sync"):
                sink["res"] = _chat(http, lane, sid, tag, timeout=60)
            else:
                drain.start()
                drain.join(timeout=60)
        except Exception as exc:
            sink["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _frames_contain(drain, text):
    try:
        return text in drain.status_texts()
    except Exception:
        return False


def _force_cleanup(http, nonce, uids, workers):
    """Best-effort release + bounded join so a held fake can never poison
    later tests via the FIFO scenario queue. Cleanup only: never asserts."""
    for uid in uids:
        try:
            _release(http, nonce, uid)
        except Exception:
            pass
    for worker in workers:
        try:
            worker.join(timeout=60)
        except Exception:
            pass


@pytest.mark.parametrize("lane", ["harness_sync", "harness_stream"])
def test_shadow_stop_cancels_held_turn(live, lane):
    manifest, http, nonce, sid = live
    tag = f"stop-{lane}-{next(_counter)}"
    held_text = f"S2 executor held {tag}"
    uid = _queue(http, nonce, "success", "late reply", hold=True,
                 status=[held_text])
    sink = {}
    worker = _post_thread(http, lane, sid, tag, sink)
    try:
        _pump_until(
            lambda: _control_state(http, nonce)["started"].get(uid, 0)
            == 1, 20000, "held fake entry")
        if lane.endswith("_stream"):
            _pump_until(
                lambda: _frames_contain(sink["drain"], held_text),
                20000, "custom status bytes before release")
        status, body = http.post("/api/chat-cancel", {"session_id": sid})
        assert status == 200, body
        _release(http, nonce, uid)
        worker.join(timeout=60)
        assert not worker.is_alive(), "request worker stuck after release"
        assert sink.get("error") is None, sink.get("error")
        if lane.endswith("_stream"):
            drain = sink["drain"]
            drain.join(timeout=60)
            assert not drain.is_alive(), "SSE drain stuck after release"
            assert drain.error is None, drain.error
            assert held_text in drain.status_texts()

        rows = _history(http, sid)
        _assert_exact_history(
            rows, [("user", _lane_message(lane, tag))],
            f"stop-cancel {lane}")
        state = _control_state(http, nonce)
        assert state["completed"].get(uid, 0) == 1, state
        assert state["blocked_attempts"] == [], state
        assert all(v == 0 for v in state["blocked_counts"].values()), state
    finally:
        _force_cleanup(http, nonce, [uid], [worker])


@pytest.mark.parametrize("lane", ["harness_sync", "harness_stream"])
def test_shadow_stop_resend_newer_keeps_lock(live, lane):
    manifest, http, nonce, sid = live
    first_tag = f"first-{lane}-{next(_counter)}"
    second_tag = f"second-{lane}-{next(_counter)}"
    first_held = f"S2 executor held {first_tag}"
    first_uid = _queue(http, nonce, "success", "first late reply",
                       hold=True, status=[first_held])
    first_sink = {}
    first_worker = _post_thread(http, lane, sid, first_tag, first_sink)
    second_uid = None
    second_worker = None
    second_sink = {}
    try:
        _pump_until(
            lambda: _control_state(http, nonce)["started"].get(
                first_uid, 0) == 1, 20000, "first fake entry")
        if lane.endswith("_stream"):
            _pump_until(
                lambda: _frames_contain(first_sink["drain"], first_held),
                20000, "first custom status before cancel")

        status, body = http.post("/api/chat-cancel", {"session_id": sid})
        assert status == 200, body

        second_uid = _queue(http, nonce, "success", "second reply",
                            hold=True,
                            status=[f"S2 executor held {second_tag}"])
        second_worker = _post_thread(
            http, lane, sid, second_tag, second_sink)
        _pump_until(
            lambda: _control_state(http, nonce)["started"].get(
                second_uid, 0) == 1, 20000, "second fake entry")

        # Release the uncooperative first fake while the newer turn holds
        # the lock: stale result persists nothing and the lock stays new.
        # `completed` alone only proves the fake returned; the joined
        # worker (+ joined drain) below proves the OLD HTTP route itself
        # finalized while the newer turn stayed busy.
        _release(http, nonce, first_uid)
        first_worker.join(timeout=60)
        assert not first_worker.is_alive(), "first worker stuck"
        assert first_sink.get("error") is None, first_sink.get("error")
        if lane.endswith("_stream"):
            first_drain = first_sink["drain"]
            first_drain.join(timeout=60)
            assert not first_drain.is_alive(), "first SSE drain stuck"
            assert first_drain.error is None, first_drain.error
        state = _control_state(http, nonce)
        assert state["completed"].get(first_uid, 0) == 1, state
        status, body = http.post("/api/chat", {
            "message": f"/cursor probe {second_tag}", "session_id": sid,
            "stream": False})
        assert status == 409, body
        rows = _history(http, sid)
        _assert_exact_history(rows, [
            ("user", _lane_message(lane, first_tag)),
            ("user", _lane_message(lane, second_tag)),
        ], f"stop-resend newer-held {lane}")

        _release(http, nonce, second_uid)
        second_worker.join(timeout=60)
        assert not second_worker.is_alive(), "second worker stuck"
        assert second_sink.get("error") is None, second_sink.get("error")
        if lane.endswith("_stream"):
            second_drain = second_sink["drain"]
            second_drain.join(timeout=60)
            assert not second_drain.is_alive(), "second SSE drain stuck"
            assert second_drain.error is None, second_drain.error
        rows = _history(http, sid)
        _assert_exact_history(rows, [
            ("user", _lane_message(lane, first_tag)),
            ("user", _lane_message(lane, second_tag)),
            ("assistant", "second reply"),
        ], f"stop-resend completed {lane}")

        # Guard check after the LAST history read of this journey.
        state = _control_state(http, nonce)
        assert state["started"].get(first_uid, 0) == 1, state
        assert state["started"].get(second_uid, 0) == 1, state
        assert state["completed"].get(first_uid, 0) == 1, state
        assert state["completed"].get(second_uid, 0) == 1, state
        assert state["blocked_attempts"] == [], state
        assert all(v == 0 for v in state["blocked_counts"].values()), state
    finally:
        uids = [first_uid] + ([second_uid] if second_uid else [])
        workers = [first_worker] + ([second_worker] if second_worker
                                    else [])
        _force_cleanup(http, nonce, uids, workers)
