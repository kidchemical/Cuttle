"""Run-wide Changes: native/step evidence reconciled with the turn snapshot.

Fixture shapes mirror recorded payloads: Codex reports a new file as its whole
body under ``{"type": "add"}``, Claude and Muse report structured hunks, and
Cursor a unified ``diffString``. No harness, network or paid model is used.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from api.agent_events.reconcile import patch_counts, reconcile, relative_path, split_patch
from api.agent_events.store import EventStore, state_dir

ROOT = "/repo"
SNAPSHOT_TEXT = (
    "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n x\n+y\n"
    "diff --git a/src/b.py b/src/b.py\n--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-old\n+new\n"
)


def _snapshot(files, overlap=(), skipped=None):
    return {"id": 1, "seq": 9, "block_id": "turn-snapshot", "source": "snapshot", "repo_root": ROOT,
            "files": files, "overlap": list(overlap), "ambiguous": bool(overlap), "skipped": skipped or {},
            "text": SNAPSHOT_TEXT, "start_sha": "s", "end_sha": "e"}


def _native(path, patch, seq=2, change="modify"):
    return {"id": seq, "seq": seq, "block_id": f"n{seq}", "source": "native", "path": path,
            "patch": patch, "change": change, "tool_id": f"tool-{seq}"}


def test_patch_counts_across_vendor_shapes():
    assert patch_counts("--- a\n+++ b\n@@ -1 +1,2 @@\n x\n+y\n") == (1, 0)
    assert patch_counts([{"oldStart": 1, "oldLines": 1, "newStart": 1, "newLines": 2, "lines": [" x", "+y", "-z"]}]) == (1, 1)
    assert patch_counts("line one\nline two\n", {"type": "add"}) == (2, 0)
    assert patch_counts(None) == (None, None)


def test_relative_paths_and_split():
    assert relative_path("/repo/src/a.py", ROOT) == "src/a.py"
    assert relative_path("src/a.py", ROOT) == "src/a.py"
    assert relative_path("/elsewhere/a.py", ROOT) is None
    assert relative_path("../escape.py", ROOT) is None
    assert relative_path("./.github/x.yml", None) == ".github/x.yml"
    assert set(split_patch(SNAPSHOT_TEXT)) == {"src/a.py", "src/b.py"}


def test_consistent_when_every_change_is_reported_and_lines_match():
    edits = [_snapshot([{"path": "src/a.py", "additions": 1, "deletions": 0}]),
             _native("/repo/src/a.py", "@@ -1 +1,2 @@\n x\n+y\n")]
    model = reconcile(edits, "cursor")
    assert model["verdict"] == "consistent"
    (row,) = model["files"]
    assert row["status"] == "reported" and row["lines"] == "match" and row["native"][0]["seq"] == 2


def test_shell_write_is_unreported_for_native_capable_agents():
    edits = [_snapshot([{"path": "src/a.py", "additions": 1, "deletions": 0},
                        {"path": "src/b.py", "additions": 1, "deletions": 1}]),
             _native("src/a.py", "@@ -1 +1,2 @@\n x\n+y\n")]
    model = reconcile(edits, "codex")
    assert model["verdict"] == "unreported"
    assert {r["path"]: r["status"] for r in model["files"]} == {"src/a.py": "reported", "src/b.py": "unreported"}
    # The same turn with no evidence at all is still unreported for a native-capable agent…
    assert reconcile([_snapshot([{"path": "src/b.py", "additions": 1, "deletions": 1}])], "claude")["verdict"] == "unreported"
    # …but only a snapshot record for an agent that never reports per-step edits.
    assert reconcile([_snapshot([{"path": "src/b.py", "additions": 1, "deletions": 1}])], "hermes")["verdict"] == "snapshot_only"


def test_extra_lines_beyond_the_reported_edit_are_flagged():
    edits = [_snapshot([{"path": "src/a.py", "additions": 5, "deletions": 0}]),
             _native("src/a.py", "@@ -1 +1,2 @@\n x\n+y\n")]
    model = reconcile(edits, "claude")
    assert model["files"][0]["lines"] == "differs" and model["verdict"] == "unreported"


def test_step_snapshots_reverts_outside_paths_and_skips():
    step = {"id": 3, "seq": 3, "block_id": "step-abc", "source": "snapshot", "repo_root": ROOT,
            "text": "diff --git a/src/b.py b/src/b.py\n@@ -1 +1 @@\n-old\n+new\n", "tool_id": "muse:t"}
    edits = [_snapshot([{"path": "src/b.py", "additions": 1, "deletions": 1},
                        {"path": "img.png", "additions": None, "deletions": None}], skipped={"img.png": "binary"}),
             step, _native("src/gone.py", "@@ -1 +1 @@\n-a\n+b\n", seq=4), _native("/tmp/outside.txt", "x", seq=5)]
    model = reconcile(edits, "muse")
    status = {r["path"]: r["status"] for r in model["files"]}
    assert status == {"src/b.py": "observed", "img.png": "unverifiable", "src/gone.py": "reverted"}
    assert model["outside"][0]["path"] == "/tmp/outside.txt"
    assert model["verdict"] == "consistent"


def test_overlap_and_missing_snapshot_claim_nothing():
    edits = [_snapshot([{"path": "src/a.py", "additions": 1, "deletions": 0}], overlap=["other"])]
    assert reconcile(edits, "codex")["verdict"] == "ambiguous"
    assert reconcile([_native("src/a.py", "x")], "codex")["verdict"] == "no_snapshot"
    assert reconcile([_snapshot([])], "codex")["verdict"] == "no_changes"


@pytest.fixture
def client(owner_session):
    from api.agent_events import routes
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return owner_session.sign_in(app.test_client())


def test_changes_route_reconciles_the_whole_run(client):
    store = EventStore(state_dir())
    snapshot = _snapshot([{"path": "src/a.py", "additions": 1, "deletions": 0}])
    snapshot.pop("id"); snapshot.pop("seq")
    store.write_batch([("run", "q", {"harness": {"agent_id": "cursor"}})]
                      + [("event", "q", {"kind": "tool", "block_id": str(i)}) for i in range(150)]
                      + [("event", "q", {"kind": "edit", "block_id": "n", "source": "native", "path": "/repo/src/a.py",
                                         "patch": "@@ -1 +1,2 @@\n x\n+y\n"}),
                         ("event", "q", {"kind": "edit", **snapshot})])
    model = client.get("/api/agent-events/runs/q/changes").get_json()
    assert model["verdict"] == "consistent" and model["files"][0]["native"][0]["seq"] == 151
    assert model["snapshot_event_id"] and model["query_id"] == "q"
    assert client.get("/api/agent-events/runs/missing/changes").status_code == 404


node_only = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


@node_only
def test_changes_markup_and_patch_split_in_node():
    module = Path(__file__).resolve().parents[2] / "web" / "js" / "queries" / "query_changes.js"
    model = reconcile([_snapshot([{"path": "src/a.py", "additions": 1, "deletions": 0},
                                  {"path": "src/b.py", "additions": 1, "deletions": 1}]),
                       _native("src/a.py", "@@ -1 +1,2 @@\n x\n+y\n")], "codex")
    script = (f"const C=require({json.dumps(str(module))});const m={json.dumps(model)};"
              f"const t={json.dumps(SNAPSHOT_TEXT)};"
              "const html=C.renderHtml(m);"
              "process.stdout.write(JSON.stringify({html, split:Object.keys(C.splitPatch(t)), verdict:C.verdictText(m)}));")
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert out["split"] == ["src/a.py", "src/b.py"]
    assert "not reported" in out["verdict"]
    assert out["html"].count('class="query-changes-file"') == 2
    assert 'data-seq="2"' in out["html"] and "unreported" in out["html"] and "<script" not in out["html"]
