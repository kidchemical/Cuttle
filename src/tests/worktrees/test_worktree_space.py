"""Worktree free-space guard, settings patch and the Settings route + client patch."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from api.worktrees import service, space
from tests.worktrees.test_worktree_service import _create, _db, _repo

GB = space.GB
JS = Path(__file__).resolve().parents[3] / "src" / "web" / "js" / "settings" / "settings_worktrees.js"


def test_default_reserve_and_invalid_values():
    assert space.min_free_gb({}) == space.DEFAULT_MIN_FREE_GB
    assert space.min_free_gb({"min_free_gb": 0}) == 0
    assert space.min_free_gb({"min_free_gb": "nope"}) == space.DEFAULT_MIN_FREE_GB
    assert space.min_free_gb({"min_free_gb": -4}) == space.DEFAULT_MIN_FREE_GB


def test_shortfall_only_refuses_what_it_measured():
    kw = {"target": "/big/wt", "settings": {"min_free_gb": 10}}
    assert space.shortfall(needed_bytes=30 * GB, free=45 * GB, **kw) is None
    msg = space.shortfall(needed_bytes=30 * GB, free=35 * GB, **kw)
    assert "30.0 GB" in msg and "35.0 GB free" in msg and "keeps 10 GB free" in msg and "Settings" in msg
    assert space.shortfall(needed_bytes=None, free=5 * GB, **kw) is not None  # already under the reserve
    assert space.shortfall(needed_bytes=None, free=50 * GB, **kw) is None
    assert space.shortfall(needed_bytes=30 * GB, free=1, target="/big/wt", settings={"min_free_gb": 0}) is None


def test_create_is_refused_before_git_touches_the_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(space, "free_bytes", lambda path: 2 * GB)
    with pytest.raises(service.WorktreeError, match="Not enough free space"):
        _create(tmp_path, monkeypatch)
    assert not (tmp_path / "home" / "worktrees").exists()
    assert service.list_worktrees(1, db_path_override=_db(tmp_path)) == []


def test_create_passes_with_the_check_off(tmp_path, monkeypatch):
    monkeypatch.setattr(space, "free_bytes", lambda path: 2 * GB)
    _, checkout = _create(tmp_path, monkeypatch, settings={"min_free_gb": 0})
    assert Path(checkout.path).is_dir()


def test_patch_validates_and_remembers_replaced_folders(tmp_path):
    with pytest.raises(space.SpaceError, match="absolute"):
        space.validate_patch({"root": "relative/wt"})
    with pytest.raises(space.SpaceError, match="between 0"):
        space.validate_patch({"min_free_gb": -1})
    with pytest.raises(space.SpaceError, match="project id"):
        space.validate_patch({"project_roots": {"abc": str(tmp_path)}})
    patch = space.validate_patch({"root": str(tmp_path / "b"), "min_free_gb": "25",
                                  "project_roots": {"7": str(tmp_path / "p7"), "8": ""}})
    block = space.apply_patch({"root": str(tmp_path / "a"), "project_roots": {"8": str(tmp_path / "p8")}}, patch)
    assert block["root"] == str(tmp_path / "b") and block["min_free_gb"] == 25
    assert block["project_roots"] == {"7": str(tmp_path / "p7")}
    assert set(block["previous_roots"]) == {str(tmp_path / "a"), str(tmp_path / "p8")}


def test_describe_lists_projects_with_their_effective_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    view = space.describe({"project_roots": {"7": str(tmp_path)}},
                          [{"id": 7, "name": "Big"}, {"id": 8, "name": "Small"}])
    assert view["root"] == "" and view["effective"]["path"] == view["default_root"]
    assert view["projects"][0]["effective"]["path"] == str(tmp_path)
    assert view["projects"][0]["effective"]["free_gb"] is not None
    assert view["projects"][1]["root"] == "" and view["projects"][1]["effective"]["path"] == view["default_root"]


def test_tracked_size_sums_the_base_ref(tmp_path):
    from api import git_service
    repo = _repo(tmp_path / "primary")
    assert git_service.tracked_size(str(repo), "HEAD") == len("one\n")
    assert git_service.tracked_size(str(repo), "no-such-ref") is None


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    from tests.auth.test_http_authz import _auth_client
    monkeypatch.setenv("CUTTLE_HOME", str(tmp_path / "home"))
    from managers import settings_manager as sm
    monkeypatch.setattr(sm, "_settings_manager", sm.SettingsManager(settings_file=str(tmp_path / "settings.json")))
    from api import experimental
    flag = {"on": True}
    monkeypatch.setattr(experimental, "is_enabled", lambda flag_id: flag["on"])
    c = _auth_client(tmp_path, monkeypatch)
    c["client"].set_cookie("session_token", c["token"])
    c["flag"] = flag
    return c


def test_settings_route_round_trip(ctx, tmp_path):
    from tests.auth.test_http_authz import LAN
    client = ctx["client"]
    body = client.get("/api/settings/worktrees", environ_base=LAN).get_json()
    assert body["available"] is True and body["min_free_gb"] == 10 and body["root"] == ""
    res = client.post("/api/settings/worktrees", json={"root": str(tmp_path / "wt"), "min_free_gb": 20},
                      environ_base=LAN)
    assert res.status_code == 200
    saved = res.get_json()
    assert saved["root"] == str(tmp_path / "wt") and saved["min_free_gb"] == 20
    assert saved["effective"]["path"] == str(tmp_path / "wt")
    bad = client.post("/api/settings/worktrees", json={"root": "relative"}, environ_base=LAN)
    assert bad.status_code == 400 and "absolute" in bad.get_json()["error"]
    ctx["flag"]["on"] = False
    assert client.get("/api/settings/worktrees", environ_base=LAN).get_json() == {
        "success": True, "available": False}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_settings_client_patch_sends_only_changed_projects():
    script = (
        "const S = require(process.argv[1]);"
        "const data = { projects: [{ id: '7', root: '/a' }, { id: '8', root: '' }] };"
        "console.log(JSON.stringify({"
        " patch: S.patchFrom({ root: ' /big ', min_free_gb: '15', projects: { 7: '/a', 8: '/b' } }, data),"
        " same: S.patchFrom({ root: '', min_free_gb: 10, projects: { 7: '/a', 8: '' } }, data),"
        " note: S.folderNote({ usable: true, path: '/big', free_gb: 12.5 }),"
        " bad: S.folderNote({ usable: false }) }));"
    )
    out = json.loads(subprocess.run(["node", "-e", script, str(JS)], capture_output=True, text=True,
                                    encoding="utf-8", timeout=60, check=True).stdout)
    assert out["patch"] == {"root": "/big", "min_free_gb": 15, "project_roots": {"8": "/b"}}
    assert out["same"] == {"root": "", "min_free_gb": 10}
    assert out["note"] == "/big · 12.5 GB free" and "cannot be used" in out["bad"]
