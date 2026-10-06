"""Focused tests for the GitHub App integration (api.github_app + routes).

Covers: payload validation, config round-trip with the private key kept out
of settings.json, JWT shape/signature, the connection probe (with a fake
transport — no network), and the settings-route contract including the rule
that no endpoint ever returns key material.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from api import github_app
from tests.auth.test_http_authz import LAN, _auth_client



def _gen_key() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


PEM = _gen_key()


def _isolated(monkeypatch, tmp_path):
    """Throwaway settings file + throwaway key file (never live state)."""
    from managers import settings_manager as sm

    isolated = sm.SettingsManager(settings_file=str(tmp_path / "gh-settings.json"))
    monkeypatch.setattr(sm, "_settings_manager", isolated)
    monkeypatch.setattr(
        "api.settings_routes.get_settings_manager", lambda: isolated
    )
    monkeypatch.setattr(github_app, "KEY_PATH", tmp_path / "gh-key.pem")
    return isolated


# ── validation ────────────────────────────────────────────────────────────


def test_validate_save_accepts_full_payload():
    ok, err, clean = github_app.validate_save(
        {"app_id": "5093587", "installation_id": "123", "private_key": PEM}
    )
    assert (ok, err) == (True, "")
    assert clean["app_id"] == "5093587"
    assert "PRIVATE KEY" in clean["private_key"]


def test_validate_save_keeps_existing_key_when_blank():
    ok, _, clean = github_app.validate_save(
        {"app_id": "1", "installation_id": "2", "private_key": "  "},
        has_existing_key=True,
    )
    assert ok and "private_key" not in clean


@pytest.mark.parametrize(
    "payload,existing",
    [
        ({"app_id": "abc", "installation_id": "2", "private_key": PEM}, False),
        ({"app_id": "1", "installation_id": "", "private_key": PEM}, False),
        ({"app_id": "1", "installation_id": "2", "private_key": "not-a-key"}, False),
        ({"app_id": "1", "installation_id": "2", "private_key": ""}, False),
        ("not-a-dict", False),
    ],
)
def test_validate_save_rejects_bad_payloads(payload, existing):
    ok, err, _ = github_app.validate_save(payload, has_existing_key=existing)
    assert ok is False and err


# ── storage split ─────────────────────────────────────────────────────────


def test_save_round_trip_keeps_key_out_of_settings(monkeypatch, tmp_path):
    settings = _isolated(monkeypatch, tmp_path)
    cfg = github_app.save_config(settings, "5093587", "42", PEM)
    assert cfg == {
        "app_id": "5093587",
        "installation_id": "42",
        "key_configured": True,
        "connected": True,
        "last_verified": {},
    }
    raw = (tmp_path / "gh-settings.json").read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in raw
    assert "RSA" not in raw
    key_file = tmp_path / "gh-key.pem"
    assert "PRIVATE KEY" in key_file.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
def test_key_file_is_owner_only(monkeypatch, tmp_path):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "1", "2", PEM)
    mode = stat.S_IMODE(os.stat(tmp_path / "gh-key.pem").st_mode)
    assert mode == 0o600


def test_get_config_reports_missing_key(monkeypatch, tmp_path):
    settings = _isolated(monkeypatch, tmp_path)
    assert github_app.get_config(settings) == {
        "app_id": "",
        "installation_id": "",
        "key_configured": False,
        "connected": False,
        "last_verified": {},
    }


def test_key_path_is_gitignored():
    """The live key file must never be committable. Regression guard."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")
    repo_root = Path(__file__).resolve().parents[3]
    rel = github_app.KEY_PATH.relative_to(repo_root).as_posix()
    res = subprocess.run(
        ["git", "check-ignore", "-q", rel],
        cwd=repo_root,
        capture_output=True,
    )
    assert res.returncode == 0, f"{rel} is not covered by .gitignore"


def test_key_path_lives_in_designated_secret_dir():
    """Key material belongs in the install-local secrets dir."""
    repo_root = Path(__file__).resolve().parents[3]
    assert github_app.KEY_PATH.relative_to(repo_root).as_posix().startswith(
        ".cuttle/personal/secrets/"
    )


def test_remove_config_clears_everything(monkeypatch, tmp_path):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "5093587", "42", PEM)
    assert (tmp_path / "gh-key.pem").exists()
    cfg = github_app.remove_config(settings)
    assert cfg["connected"] is False
    assert cfg["key_configured"] is False
    assert not (tmp_path / "gh-key.pem").exists()
    assert "PRIVATE KEY" not in (tmp_path / "gh-settings.json").read_text(
        encoding="utf-8"
    )


def test_successful_probe_is_remembered(monkeypatch, tmp_path):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "5093587", "42", _gen_key())

    def fake(method, url, token, payload):
        if url.endswith("/access_tokens"):
            return {"token": "ghs_x", "expires_at": "x"}
        if "/users/" in url:
            return {"id": 987654, "login": "cuttle-harness[bot]", "type": "Bot"}
        return {"app_slug": "cuttle-harness", "account": {"login": "kidchemical"}}

    github_app.test_connection(settings, api_call=fake)
    cfg = github_app.get_config(settings)
    assert cfg["connected"] is True
    assert cfg["last_verified"]["app_slug"] == "cuttle-harness"
    assert cfg["last_verified"]["account"] == "kidchemical"
    assert "PRIVATE KEY" not in json.dumps(cfg)


def test_account_tab_has_entry_and_setup_views():
    html = Path(__file__).resolve().parents[3] / "src" / "web" / "settings_page.html"
    text = html.read_text(encoding="utf-8")
    for element_id in (
        "githubAppEntry",
        "githubAppSetup",
        "githubAppEntryTitle",
        "githubAppEntryDetail",
        "githubAppEntryStatus",
        "githubAppCancelBtn",
    ):
        assert f'id="{element_id}"' in text, element_id
    assert "githubAppRemove()" in text
    assert "githubAppEdit()" in text


def test_bot_identity_format():
    ident = github_app.bot_identity("5093587", "cuttle-harness")
    assert ident == {
        "name": "cuttle-harness[bot]",
        "email": "5093587+cuttle-harness[bot]@users.noreply.github.com",
    }


# ── JWT / token ───────────────────────────────────────────────────────────


def _b64d(seg: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(seg + "==="))


def test_mint_jwt_shape_and_signature():
    pem = _gen_key()
    token = github_app.mint_jwt("5093587", pem, now=1_700_000_000)
    header, payload, sig = token.split(".")
    assert _b64d(header)["alg"] == "RS256"
    body = _b64d(payload)
    assert body["iss"] == "5093587"
    assert body["exp"] - body["iat"] == 600

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pub = serialization.load_pem_private_key(pem.encode(), password=None).public_key()
    pub.verify(
        base64.urlsafe_b64decode(sig + "==="),
        f"{header}.{payload}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_request_installation_token_uses_jwt():
    seen = {}

    def fake(method, url, token, payload):
        seen.update(method=method, url=url, jwt_parts=len(token.split(".")))
        return {"token": "ghs_fake", "expires_at": "2026-01-01T00:00:00Z"}

    token, expires = github_app.request_installation_token(
        "1", "2", _gen_key(), api_call=fake
    )
    assert (token, expires) == ("ghs_fake", "2026-01-01T00:00:00Z")
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/app/installations/2/access_tokens")
    assert seen["jwt_parts"] == 3


def test_request_installation_token_refuses_empty():
    with pytest.raises(RuntimeError):
        github_app.request_installation_token(
            "1", "2", _gen_key(), api_call=lambda *a: {}
        )


def test_connection_probe_returns_facts_not_token(monkeypatch, tmp_path):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "5093587", "42", _gen_key())

    def fake(method, url, token, payload):
        if url.endswith("/access_tokens"):
            return {"token": "ghs_secret", "expires_at": "x"}
        if "/users/" in url:
            return {"id": 987654, "login": "cuttle-harness[bot]", "type": "Bot"}
        return {"app_slug": "cuttle-harness", "account": {"login": "kidchemical"}}

    result = github_app.test_connection(settings, api_call=fake)
    assert result["success"] is True
    assert result["app_slug"] == "cuttle-harness"
    assert result["account"] == "kidchemical"
    assert "ghs_secret" not in json.dumps(result)
    assert "987654+cuttle-harness[bot]" in result["commit_as"]


def test_connection_probe_needs_saved_config(monkeypatch, tmp_path):
    settings = _isolated(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        github_app.test_connection(settings, api_call=lambda *a: {})


# ── route contract ────────────────────────────────────────────────────────


def test_github_app_routes_never_return_key_material(tmp_path, monkeypatch):
    _isolated(monkeypatch, tmp_path)
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])

    res = ctx["client"].get("/api/settings/github-app", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["github_app"]["key_configured"] is False

    res = ctx["client"].post(
        "/api/settings/github-app",
        json={"app_id": "5093587", "installation_id": "42", "private_key": PEM},
        environ_base=LAN,
    )
    assert res.status_code == 200, res.get_json()
    assert "PRIVATE KEY" not in json.dumps(res.get_json())

    res = ctx["client"].get("/api/settings/github-app", environ_base=LAN)
    assert res.get_json()["github_app"]["key_configured"] is True

    res = ctx["client"].post(
        "/api/settings/github-app",
        json={"app_id": "x", "installation_id": "42"},
        environ_base=LAN,
    )
    assert res.status_code == 400


def test_github_app_delete_disconnects(tmp_path, monkeypatch):
    _isolated(monkeypatch, tmp_path)
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])

    ctx["client"].post(
        "/api/settings/github-app",
        json={"app_id": "5093587", "installation_id": "42", "private_key": PEM},
        environ_base=LAN,
    )
    res = ctx["client"].delete("/api/settings/github-app", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["github_app"]["connected"] is False

    res = ctx["client"].get("/api/settings/github-app", environ_base=LAN)
    assert res.get_json()["github_app"]["connected"] is False


def test_github_app_test_probe_reports_failure_without_network(
    tmp_path, monkeypatch
):
    _isolated(monkeypatch, tmp_path)
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])

    res = ctx["client"].post("/api/settings/github-app/test", environ_base=LAN)
    assert res.status_code == 200
    assert res.get_json()["success"] is False  # nothing saved yet, no network


def test_github_app_route_auth_matrix(tmp_path, monkeypatch):
    _isolated(monkeypatch, tmp_path)
    from api import web_chat_api as wca

    anon = wca.app.test_client()
    assert (
        anon.get("/api/settings/github-app", environ_base=LAN).status_code == 401
    )
    assert (
        anon.post("/api/settings/github-app", json={}, environ_base=LAN).status_code
        == 401
    )
    assert (
        anon.post("/api/settings/github-app/test", environ_base=LAN).status_code
        == 401
    )

    guest = wca.app.test_client()
    assert guest.post("/api/auth/guest", json={}).status_code == 200
    assert (
        guest.post(
            "/api/settings/github-app", json={}, environ_base=LAN
        ).status_code
        == 403
    )


@pytest.mark.parametrize("invalid", [
    "-----BEGIN RSA PRIVATE KEY-----\ngarbage\n-----END RSA PRIVATE KEY-----",
    "BEGIN PRIVATE KEY garbage",
])
def test_invalid_replacement_preserves_credentials(tmp_path, monkeypatch, invalid):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "1", "2", PEM)
    before_config = settings.get_setting(github_app.SETTINGS_KEY).copy()
    before_file = github_app.KEY_PATH.read_bytes()
    before_settings_file = (tmp_path / "gh-settings.json").read_bytes()
    with pytest.raises(ValueError, match="RSA"):
        github_app.save_config(settings, "3", "4", invalid)
    assert github_app.KEY_PATH.read_bytes() == before_file
    assert settings.get_setting(github_app.SETTINGS_KEY) == before_config
    assert (tmp_path / "gh-settings.json").read_bytes() == before_settings_file


def test_non_rsa_key_is_rejected():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    ok, error, _ = github_app.validate_save({
        "app_id": "1", "installation_id": "2", "private_key": pem,
    })
    assert not ok and "RSA" in error


def test_key_write_failure_preserves_settings(tmp_path, monkeypatch):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "1", "2", PEM)
    before = settings.get_setting(github_app.SETTINGS_KEY).copy()
    original_key = github_app.KEY_PATH.read_bytes()
    monkeypatch.setattr(github_app, "_write_key_atomically", lambda *a: (_ for _ in ()).throw(OSError("write failed")))
    with pytest.raises(OSError):
        github_app.save_config(settings, "3", "4", PEM)
    assert settings.get_setting(github_app.SETTINGS_KEY) == before
    assert github_app.KEY_PATH.read_bytes() == original_key


def test_route_rejects_malformed_key_without_overwriting(tmp_path, monkeypatch):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "1", "2", PEM)
    original_key = github_app.KEY_PATH.read_bytes()
    original_config = settings.get_setting(github_app.SETTINGS_KEY).copy()
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])
    response = ctx["client"].post("/api/settings/github-app", environ_base=LAN, json={
        "app_id": "3", "installation_id": "4",
        "private_key": "-----BEGIN PRIVATE KEY-----\ngarbage\n-----END PRIVATE KEY-----",
    })
    assert response.status_code == 400
    assert github_app.KEY_PATH.read_bytes() == original_key
    assert settings.get_setting(github_app.SETTINGS_KEY) == original_config


def test_settings_failure_rolls_back_valid_key_replacement(tmp_path, monkeypatch):
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "1", "2", PEM)
    original_key = github_app.KEY_PATH.read_bytes()
    original_file = (tmp_path / "gh-settings.json").read_bytes()
    original_config = settings.get_setting(github_app.SETTINGS_KEY).copy()
    set_setting = settings.set_setting

    def fail_new_ids(name, value):
        if name == github_app.SETTINGS_KEY and value.get("app_id") == "3":
            settings.settings[name] = value
            return False
        return set_setting(name, value)

    monkeypatch.setattr(settings, "set_setting", fail_new_ids)
    with pytest.raises(RuntimeError, match="Could not save"):
        github_app.save_config(settings, "3", "4", _gen_key())
    assert github_app.KEY_PATH.read_bytes() == original_key
    assert settings.get_setting(github_app.SETTINGS_KEY) == original_config
    assert (tmp_path / "gh-settings.json").read_bytes() == original_file


@pytest.mark.parametrize("commit_path", ["ui", "task"])
@pytest.mark.parametrize("remote", ["https://github.com/kidchemical/project.git", "git@github.com:kidchemical/project.git", "ssh://git@github.com/kidchemical/project.git"])
def test_real_commits_use_verified_bot_identity(monkeypatch, tmp_path, commit_path, remote):
    import subprocess
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "5093587", "42", PEM)

    def fake(method, url, token, payload):
        if url.endswith("/access_tokens"):
            return {"token": "ghs_fake"}
        if "/users/" in url:
            return {"id": 987654, "login": "cuttle-harness[bot]", "type": "Bot"}
        return {"app_slug": "cuttle-harness", "account": {"login": "kidchemical"}}

    github_app.test_connection(settings, api_call=fake)
    monkeypatch.setattr(github_app, "_api", lambda *a: pytest.fail("Commit must not call GitHub"))
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git("init")
    git("config", "user.name", "Personal")
    git("config", "user.email", "personal@example.com")
    git("remote", "add", "origin", remote)
    (repo / "file.txt").write_text("hello")
    for name in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(name, "Environment Personal")
    for name in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "env@example.com")
    if commit_path == "ui":
        from scripts.utilities.git_pending_changes import commit_pending_changes
        commit_pending_changes(str(repo), "bot attribution")
    else:
        from api.git_service import stage_and_commit
        stage_and_commit(str(repo), "bot attribution")
    identity = "cuttle-harness[bot]|987654+cuttle-harness[bot]@users.noreply.github.com"
    assert git("show", "-s", "--format=%an|%ae|%cn|%ce") == identity + "|" + identity
    assert git("config", "user.name") == "Personal"
    assert git("config", "user.email") == "personal@example.com"


@pytest.mark.parametrize("commit_path", ["ui", "task"])
def test_unverified_app_rejects_before_staging(monkeypatch, tmp_path, commit_path):
    import subprocess
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "5093587", "42", PEM)
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    git("init")
    git("remote", "add", "origin", "https://github.com/kidchemical/project.git")
    (repo / "file.txt").write_text("hello")
    from api.git_service import GitError, stage_and_commit
    from scripts.utilities.git_pending_changes import commit_pending_changes
    with pytest.raises((ValueError, GitError), match="Re-check"):
        (commit_pending_changes if commit_path == "ui" else stage_and_commit)(str(repo), "blocked")
    assert git("diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("remote", ["https://gitlab.com/kidchemical/project.git", "https://github.com/someone-else/project.git"])
def test_commit_identity_is_scoped(monkeypatch, tmp_path, remote):
    import subprocess
    settings = _isolated(monkeypatch, tmp_path)
    github_app.save_config(settings, "5093587", "42", PEM)
    stored = settings.get_setting(github_app.SETTINGS_KEY)
    stored["last_verified"] = {"app_id": "5093587", "installation_id": "42", "account": "kidchemical", "app_slug": "cuttle-harness", "bot_user_id": "987654"}
    settings.set_setting(github_app.SETTINGS_KEY, stored)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote], cwd=repo, check=True)
    assert github_app.commit_env(str(repo), os.environ.copy()) is None
