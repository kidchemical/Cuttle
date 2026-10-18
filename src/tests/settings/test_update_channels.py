"""api.updates: install kind, Developer mode, and checkout channel status.

Git cases run against throwaway repositories (a bare "origin" plus clones);
nothing touches the real checkout, GitHub, or live settings.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.auth.test_http_authz import _auth_client, LAN


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name)


@pytest.fixture
def repos(tmp_path):
    """origin (bare) <- upstream clone pushes; work clone tracks origin/dev."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    upstream = tmp_path / "upstream"
    _git(tmp_path, "clone", "-q", str(origin), str(upstream))
    _git(upstream, "checkout", "-qb", "dev")
    _commit(upstream, "a.txt")
    _git(upstream, "push", "-q", "-u", "origin", "dev")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "-b", "dev", str(origin), str(work))
    return upstream, work


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    from managers import settings_manager as sm

    isolated = sm.SettingsManager(settings_file=str(tmp_path / "settings.json"))
    monkeypatch.setattr(sm, "_settings_manager", isolated)
    return isolated


def test_install_kind_follows_git_metadata(tmp_path):
    from api import updates

    assert updates.install_kind(tmp_path) == "release"
    (tmp_path / ".git").write_text("gitdir: elsewhere", encoding="utf-8")  # linked worktree
    assert updates.install_kind(tmp_path) == "source"


def test_developer_mode_defaults_from_install_and_persists(tmp_path, monkeypatch, isolated_settings):
    from api import updates

    assert updates.developer_mode("release") == {"enabled": False, "available": False, "explicit": False}
    assert updates.developer_mode("source") == {"enabled": True, "available": True, "explicit": False}

    monkeypatch.setattr(updates, "install_kind", lambda root=None: "source")
    assert updates.set_developer_mode(False)["enabled"] is False
    assert isolated_settings.get_setting("updates") == {"developer_mode": False}
    assert updates.developer_mode("source") == {"enabled": False, "available": True, "explicit": True}
    # A stored "on" never leaks into a release install.
    updates.set_developer_mode(True)
    assert updates.developer_mode("release")["enabled"] is False

    monkeypatch.setattr(updates, "install_kind", lambda root=None: "release")
    with pytest.raises(ValueError):
        updates.set_developer_mode(True)
    with pytest.raises(ValueError):
        updates.set_developer_mode("yes")


def test_checkout_status_counts_only_after_fetch(repos):
    from api import updates

    upstream, work = repos
    _commit(upstream, "b.txt")
    _commit(upstream, "c.txt")
    _git(upstream, "push", "-q")

    stale = updates.checkout_status(work)
    assert stale["branch"] == "dev" and stale["upstream"] == "origin/dev"
    assert (stale["behind"], stale["fetched"], stale["blocked"]) == (0, False, "")

    fresh = updates.checkout_status(work, fetch=True)
    assert fresh["fetched"] is True and fresh["behind"] == 2 and fresh["ahead"] == 0
    assert [c["subject"] for c in fresh["commits"]] == ["c.txt", "b.txt"]
    assert fresh["blocked"] == "" and fresh["path"] == str(work)
    # Checking never moves the working branch.
    assert _git(work, "rev-parse", "HEAD") != _git(work, "rev-parse", "origin/dev")


def test_checkout_status_reports_what_would_block_a_pull(repos):
    from api import updates

    upstream, work = repos
    _commit(upstream, "b.txt")
    _git(upstream, "push", "-q")
    _git(work, "fetch", "-q")

    (work / "scratch.txt").write_text("wip", encoding="utf-8")
    assert "Local changes" in updates.checkout_status(work)["blocked"]
    (work / "scratch.txt").unlink()

    _commit(work, "local.txt")
    diverged = updates.checkout_status(work)
    assert (diverged["ahead"], diverged["behind"]) == (1, 1)
    assert "diverge" in diverged["blocked"]

    _git(work, "checkout", "-q", "--detach")
    assert "Detached" in updates.checkout_status(work)["blocked"]

    _git(work, "checkout", "-qb", "solo")
    assert "no upstream" in updates.checkout_status(work)["blocked"]


def test_dirty_checkout_with_nothing_to_pull_is_not_blocked(repos):
    from api import updates

    _, work = repos
    (work / "scratch.txt").write_text("wip", encoding="utf-8")
    status = updates.checkout_status(work)
    assert status["dirty"] is True and status["blocked"] == ""


def _owner(tmp_path, monkeypatch):
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    return ctx["client"]


def test_updates_routes_snapshot_and_developer_toggle(tmp_path, monkeypatch, repos, isolated_settings):
    from api import updates

    _, work = repos
    monkeypatch.setenv("CUTTLE_REPO_ROOT", str(work))
    monkeypatch.setattr("api.releases.check_releases",
                        lambda force=False: (_ for _ in ()).throw(AssertionError("no release check")))
    client = _owner(tmp_path, monkeypatch)

    body = client.get("/api/settings/updates?release=0", environ_base=LAN).get_json()
    assert body["install_kind"] == "source" and body["release"] is None
    assert body["developer_mode"]["enabled"] is True
    assert body["checkout"]["branch"] == "dev" and body["checkout"]["path"] == str(work)

    res = client.post("/api/settings/developer-mode", json={"enabled": False}, environ_base=LAN)
    assert res.status_code == 200 and res.get_json()["developer_mode"]["enabled"] is False
    body = client.get("/api/settings/updates?release=0", environ_base=LAN).get_json()
    assert body["checkout"] is None  # no branch status without Developer mode

    assert client.post("/api/settings/developer-mode", json={"enabled": "on"},
                       environ_base=LAN).status_code == 400
    monkeypatch.setattr(updates, "install_kind", lambda root=None: "release")
    res = client.post("/api/settings/developer-mode", json={"enabled": True}, environ_base=LAN)
    assert res.status_code == 400 and "source checkout" in res.get_json()["error"]


def test_explicit_check_forces_release_and_fetch(tmp_path, monkeypatch, repos, isolated_settings):
    upstream, work = repos
    _commit(upstream, "b.txt")
    _git(upstream, "push", "-q")
    monkeypatch.setenv("CUTTLE_REPO_ROOT", str(work))
    forced = []
    monkeypatch.setattr("api.releases.check_releases",
                        lambda force=False: forced.append(force) or {"state": "latest", "release": None})
    client = _owner(tmp_path, monkeypatch)

    body = client.post("/api/settings/updates/check", json={}, environ_base=LAN).get_json()
    assert forced == [True]
    assert body["checkout"]["fetched"] is True and body["checkout"]["behind"] == 1


def test_updates_read_is_owner_only(tmp_path, monkeypatch):
    from api import web_chat_api as wca

    assert wca.app.test_client().get("/api/settings/updates", environ_base=LAN).status_code == 401
    ctx = _auth_client(tmp_path, monkeypatch)
    monkeypatch.setenv("OWNER_USER_EMAIL", "owner@local")
    ctx["client"].set_cookie("session_token", ctx["other_token"])
    assert ctx["client"].get("/api/settings/updates", environ_base=LAN).status_code == 403
