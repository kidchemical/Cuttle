"""Mesh SSH pairing: no shared trusted key, per-install identities (Issue 8)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
KEYS_DIR = REPO / ".cuttle_global" / "keys"
PS1 = REPO / ".cuttle_global" / "scripts" / "install-cuttle-mesh-lan-key.ps1"
SH = REPO / ".cuttle_global" / "scripts" / "install-cuttle-mesh-lan-key.sh"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def test_no_shared_trusted_key_distributed():
    tracked = _git("ls-files", ".cuttle_global/keys").split()
    assert tracked == []  # The retired directory has no shipped files.
    assert not (KEYS_DIR / "cuttle_mesh_lan.pub").exists()


def test_key_directory_ignores_key_material():
    # Root ignore rules protect both current and legacy locations even when
    # those directories and their old nested .gitignore do not exist.
    for directory in (".cuttle_global/keys", ".cuttle/keys"):
        for filename in ("probe.pub", "probe", ".gitignore"):
            probe = _git("check-ignore", "-q", f"{directory}/{filename}")
            assert probe == ""  # check-ignore -q prints nothing on match


def test_windows_pairing_requires_explicit_peer_key():
    text = PS1.read_text(encoding="utf-8")
    # The repo-shared default trust path is gone.
    assert "keys\\cuttle_mesh_lan.pub" not in text
    assert "keys/cuttle_mesh_lan.pub" not in text
    assert "cuttle-mesh-lan-only" not in text
    # Pairing without an explicit peer key fails closed.
    assert "never installs a default/shared key" in text
    # Per-install identity generation without overwriting.
    assert "-Generate" in text and "ssh-keygen" in text
    assert "never overwritten" in text
    # Existing entries (including legacy shared lines) are preserved.
    assert "cuttle-mesh-peer" in text


def test_posix_pairing_script_present_and_explicit():
    text = SH.read_text(encoding="utf-8")
    assert "--generate" in text and "--pubkey" in text
    assert "never installs a default/shared key" in text
    assert "cuttle-mesh-peer" in text


bash_only = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not available"
)


@bash_only
def test_posix_pairing_generate_authorize_idempotent(tmp_path):
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen not available")
    home_a, home_b = tmp_path / "a", tmp_path / "b"
    home_a.mkdir()
    env_a = {"HOME": str(home_a), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        ["bash", str(SH), "--generate"], check=True, capture_output=True, env=env_a
    )
    pub = home_a / ".ssh" / "cuttle_mesh_lan.pub"
    assert pub.is_file()
    before = (home_a / ".ssh" / "cuttle_mesh_lan").read_bytes()
    subprocess.run(
        ["bash", str(SH), "--generate"], check=True, capture_output=True, env=env_a
    )
    assert (home_a / ".ssh" / "cuttle_mesh_lan").read_bytes() == before

    home_b.mkdir()
    ak = home_b / ".ssh" / "authorized_keys"
    ak.parent.mkdir(parents=True, exist_ok=True)
    ak.write_text("legacy-entry ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIlegacy\n", encoding="utf-8")
    env_b = {"HOME": str(home_b), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        ["bash", str(SH), "--pubkey", str(pub), "--alias", "tower"],
        check=True,
        capture_output=True,
        env=env_b,
    )
    body = ak.read_text(encoding="utf-8")
    assert "cuttle-mesh-peer" in body
    assert 'from="192.168.0.0/16,127.0.0.1,::1"' in body
    assert "legacy-entry" in body  # preserved, never purged
    subprocess.run(
        ["bash", str(SH), "--pubkey", str(pub), "--alias", "tower"],
        check=True,
        capture_output=True,
        env=env_b,
    )
    assert ak.read_text(encoding="utf-8").count("cuttle-mesh-peer") == 1
