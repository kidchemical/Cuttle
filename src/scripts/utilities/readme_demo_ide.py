"""Demo project for README/site IDE captures (no real code in the shot).

The IDE API is owner-only because it reads and writes project files, so the
demo guest cannot use it. Captures write this tiny Flask project to a scratch
folder and answer the IDE's /api/ide and /api/agent-review calls in the
browser from it, using the real IDE and agent-review helpers for the shapes.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FILES = {
    'api/upload.py': '''"""Upload endpoint: stores files for the signed-in user."""
from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

from api.auth import current_session_id, current_user_id
from api.limits import Limiter
from api.storage import save_upload

bp = Blueprint("upload", __name__)
upload_limiter = Limiter(limit=10, window_s=60)


def _limit_key() -> str:
    # Guests have no user id, so key them on their session instead.
    user = current_user_id()
    return f"user:{user}" if user is not None else f"session:{current_session_id()}"


@bp.post("/api/upload")
def upload():
    allowed, retry_after = upload_limiter.hit(_limit_key())
    if not allowed:
        resp = jsonify(error="Too many uploads, try again soon.")
        resp.status_code = 429
        resp.headers["Retry-After"] = str(math.ceil(retry_after))
        return resp

    file = request.files.get("file")
    if file is None:
        return jsonify(error="No file in the request."), 400
    stored = save_upload(file, owner=current_user_id())
    return jsonify(id=stored.id, size=stored.size), 201
''',
    'api/limits.py': '''"""Shared request limiter for the Demo App API."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class Limiter:
    """Sliding-window limiter keyed by user or session id."""

    limit: int
    window_s: float = 60.0
    _hits: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def hit(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """Record a request; return (allowed, seconds until the window resets)."""
        now = time.monotonic() if now is None else now
        hits = self._hits[key]
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, self.window_s - (now - hits[0])
        hits.append(now)
        return True, 0.0
''',
    'api/auth.py': '''"""Session helpers."""
from flask import g


def current_user_id():
    return getattr(g, "user_id", None)


def current_session_id():
    return getattr(g, "session_id", "anon")
''',
    'api/storage.py': '''"""Blob storage for uploads."""
from dataclasses import dataclass
from uuid import uuid4


@dataclass
class Stored:
    id: str
    size: int


def save_upload(file, owner=None) -> Stored:
    data = file.read()
    return Stored(id=uuid4().hex, size=len(data))
''',
    'api/__init__.py': '''"""Demo App API."""
''',
    'tests/test_upload_limits.py': '''from api.limits import Limiter


def test_allows_up_to_the_limit():
    lim = Limiter(limit=10, window_s=60)
    assert all(lim.hit("u", now=i)[0] for i in range(10))


def test_eleventh_request_is_rejected():
    lim = Limiter(limit=10, window_s=60)
    for i in range(10):
        lim.hit("u", now=i)
    allowed, retry = lim.hit("u", now=10)
    assert not allowed and retry == 50


def test_window_resets():
    lim = Limiter(limit=1, window_s=60)
    lim.hit("u", now=0)
    assert lim.hit("u", now=61)[0]
''',
    'web/upload.js': '''export async function upload(file) {
  const body = new FormData();
  body.append('file', file);
  const res = await fetch('/api/upload', { method: 'POST', body });
  if (res.status === 429) {
    const wait = Number(res.headers.get('Retry-After') || 1);
    throw new Error(`Rate limited, retry in ${wait}s`);
  }
  return res.json();
}
''',
    'README.md': '''# Demo App

A small Flask API used for Cuttle demos.
''',
    'requirements.txt': '''flask>=3.0
pytest>=8
''',
}

# What api/upload.py looked like before the "agent" turn, so the editor shows
# Keep / Undo hunks for the rate-limit change.
BASELINE = {"api/upload.py": '''"""Upload endpoint: stores files for the signed-in user."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from api.auth import current_user_id
from api.storage import save_upload

bp = Blueprint("upload", __name__)


@bp.post("/api/upload")
def upload():
    file = request.files.get("file")
    if file is None:
        return jsonify(error="No file in the request."), 400
    stored = save_upload(file, owner=current_user_id())
    return jsonify(id=stored.id, size=stored.size), 201
'''}

GHOST = 'resp.headers["X-RateLimit-Limit"] = str(upload_limiter.limit)'


def materialize(root: Path) -> Path:
    """Write the demo project under ``root`` and return its resolved path."""
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root.resolve()


def page_url(root: Path) -> str:
    return f"/ide_page.html?path={root}"


def seed(root: Path, chat: int | None = None, *, chat_open: bool = True) -> dict:
    """localStorage for the IDE: open tabs, expanded folders, layout, bound chat."""
    path = str(root)
    state = {"tabs": [{"path": "api/upload.py"}, {"path": "api/limits.py"}, {"path": "tests/test_upload_limits.py"}],
             "active": "api/upload.py", "mru": ["api/upload.py"], "expanded": ["api", "tests"], "recent": [],
             "chatSession": str(chat) if chat else None}
    return {
        "cuttleIdeLastWorkspace": path,
        f"cuttleIdeWorkspace:{path}": json.dumps(state),
        "cuttleIdeLayout": json.dumps({"view": "explorer", "sidebar": True, "sidebarWidth": 230, "panel": False,
                                       "chat": chat_open, "chatWidth": 440, "completions": True,
                                       "minimap": True, "wordWrap": False}),
    }


def install_routes(ctx, root: Path) -> None:
    """Answer the IDE and agent-review APIs from ``root``. Register after any catch-all route."""
    from api.agent_review import hunks as H
    from api.ide import service

    ws = {"id": "demo", "name": "Demo App", "path": str(root), "available": True, "current": True}

    def ok(route, **body):
        route.fulfill(json={"success": True, **body})

    def ide(route):
        req = route.request
        url = urlparse(req.url)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        verb = url.path.rsplit("/", 1)[-1]
        if verb == "workspaces":
            return ok(route, workspaces=[ws])
        if verb == "tree":
            return ok(route, **service.list_dir(root, q.get("dir") or ""))
        if verb == "files":
            return ok(route, **service.list_files(root))
        if verb == "file" and req.method == "GET":
            return ok(route, file=service.read_file(root, q.get("file") or ""))
        if verb == "stat":
            return ok(route, files=service.stat_files(root, (req.post_data_json or {}).get("files") or []))
        if verb == "original":
            return ok(route, original=BASELINE.get(q.get("file") or ""))
        if verb == "complete":
            return ok(route, text=GHOST, available=True)
        return ok(route)

    def review_item(rel: str) -> dict:
        base, cur = BASELINE[rel], (root / rel).read_text()
        hunk_list = H.compute(base, cur)
        return {
            "path": rel, "rel_path": rel, "repo_root": str(root), "change": "modified", "hunks": len(hunk_list),
            "additions": sum(h["new_count"] for h in hunk_list), "deletions": sum(h["old_count"] for h in hunk_list),
            "mixed": False, "chat_session_id": None, "agent_id": "cursor", "query_ids": ["demo"],
            "opened_at": 0, "updated_at": 0, "live": False,
            "baseline": base, "current": cur, "hunk_list": hunk_list, "etag": None,
        }

    def agent_review(route):
        verb = urlparse(route.request.url).path.rsplit("/", 1)[-1]
        if verb == "stream":
            return route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=": idle\n\n")
        item = review_item("api/upload.py")
        if verb == "files":
            listing = {k: v for k, v in item.items() if k not in ("baseline", "current", "hunk_list", "etag")}
            return ok(route, repo_root=str(root), files=[listing], revision=1,
                      totals={k: item[k] for k in ("hunks", "additions", "deletions")})
        if verb == "file":
            return ok(route, review=item)
        return ok(route)

    def flags(route):
        resp = route.fetch()
        data = resp.json()
        for flag in data.get("flags") or []:
            if flag.get("id") in ("ide", "agent_review"):
                flag["enabled"] = True
        route.fulfill(response=resp, json=data)

    ctx.route("**/api/ide/**", ide)
    ctx.route("**/api/agent-review/**", agent_review)
    ctx.route("**/api/experimental/flags*", flags)
    ctx.route("**/api/git/pending-changes*", lambda route: ok(route, files=[], changes=[]))
