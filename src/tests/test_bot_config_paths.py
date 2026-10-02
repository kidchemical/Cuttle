"""B2 path contract: BotConfig resolves checkout ``src/bot_config.json``.

Launch cwd must never select the file: repo-root and ``src/`` cwds resolve
identically through the production resolver, divergent files stay untouched,
a missing file creates nothing on read/import, and only an explicit save
writes the canonical path. Unknown keys round-trip; existing setter
validation is unchanged.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.config import BotConfig
import core.config as core_config_mod

WORKTREE_SRC = Path(__file__).resolve().parents[1]

_CANON_MARK = "canon-marker"
_ROOT_MARK = "root-legacy-marker"


def _stage_candidate(tmp_path: Path, *, with_canonical=True) -> Path:
    """Isolated copied source that doubles as the candidate checkout.

    Mirrors the production depth (``cand/src/core/*.py``) verbatim, so
    ``runtime_paths._repo_root()`` resolves to ``cand`` and the canonical
    path to ``cand/src/bot_config.json`` — exercising the production
    resolver with zero live imports.
    """
    pkgroot = tmp_path / "cand"
    core_dir = pkgroot / "src" / "core"
    core_dir.mkdir(parents=True)
    for name in ("__init__.py", "config.py", "runtime_paths.py"):
        shutil.copyfile(WORKTREE_SRC / "core" / name, core_dir / name)
    assert (core_dir / "config.py").read_text() == (
        WORKTREE_SRC / "core" / "config.py"
    ).read_text()
    # Divergent legacy root copy: must never be read, merged, or modified.
    (pkgroot / "bot_config.json").write_text(
        json.dumps({"mode": "regex_only", _ROOT_MARK: "root"}), encoding="utf-8"
    )
    if with_canonical:
        (pkgroot / "src" / "bot_config.json").write_text(
            json.dumps({"mode": "llm_only", _CANON_MARK: "canon"}),
            encoding="utf-8",
        )
    return pkgroot


def _scrubbed_env(pkgroot: Path) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.endswith("_KEY") and "TOKEN" not in k and "SECRET" not in k
    }
    env["B2_PKGROOT"] = str(pkgroot / "src")
    env.pop("PYTHONPATH", None)
    return env


_PROBE = (
    "import sys, os; sys.path.insert(0, os.environ['B2_PKGROOT']);"
    "from core.config import BotConfig;"
    "c = BotConfig();"
    "print('path=' + str(c.config_file.resolve()));"
    "print('canon=' + str(c.get('canon-marker')));"
    "print('mode=' + str(c.get_mode()))"
)


def _canonical(pkgroot: Path) -> Path:
    """Where the production resolver roots the copied package under test."""
    return pkgroot / "src" / "bot_config.json"


def _run_probe(pkgroot: Path, cwd: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        env=_scrubbed_env(pkgroot),
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    out = dict(
        line.split("=", 1) for line in proc.stdout.strip().splitlines()
    )
    return out


def test_resolver_selects_checkout_src_from_repo_root_cwd(tmp_path):
    pkgroot = _stage_candidate(tmp_path)
    out = _run_probe(pkgroot, pkgroot)
    assert out["path"] == str(_canonical(pkgroot))
    assert out["canon"] == "canon"
    assert out["mode"] == "llm_only"


def test_resolver_selects_checkout_src_from_src_cwd(tmp_path):
    pkgroot = _stage_candidate(tmp_path)
    out = _run_probe(pkgroot, pkgroot / "src")
    assert out["path"] == str(_canonical(pkgroot))
    assert out["canon"] == "canon"


def test_divergent_files_unchanged(tmp_path):
    pkgroot = _stage_candidate(tmp_path)
    root_before = (pkgroot / "bot_config.json").read_bytes()
    canon_before = (pkgroot / "src" / "bot_config.json").read_bytes()
    _run_probe(pkgroot, pkgroot)
    _run_probe(pkgroot, pkgroot / "src")
    assert (pkgroot / "bot_config.json").read_bytes() == root_before
    assert (pkgroot / "src" / "bot_config.json").read_bytes() == canon_before


def test_missing_file_read_creates_nothing(tmp_path):
    pkgroot = _stage_candidate(tmp_path, with_canonical=False)
    out = _run_probe(pkgroot, pkgroot)
    assert out["path"] == str(_canonical(pkgroot))
    assert out["mode"] == "default"
    assert list(pkgroot.rglob("bot_config.json")) == [pkgroot / "bot_config.json"]


_SAVE_PROBE = (
    "import sys, os; sys.path.insert(0, os.environ['B2_PKGROOT']);"
    "from core.config import BotConfig;"
    "c = BotConfig();"
    "print('saved=' + str(c.set_agent_name('Shadow')));"
    "print('path=' + str(c.config_file.resolve()));"
    "print('back=' + str(BotConfig().get_agent_name()))"
)


def test_default_save_reaches_canonical_path_from_both_cwds(tmp_path):
    """Default BotConfig().set/save lands on the resolver path, either cwd."""
    pkgroot = _stage_candidate(tmp_path, with_canonical=False)
    root_before = (pkgroot / "bot_config.json").read_bytes()
    for run_cwd in (pkgroot, pkgroot / "src"):
        proc = subprocess.run(
            [sys.executable, "-c", _SAVE_PROBE],
            cwd=str(run_cwd),
            capture_output=True,
            text=True,
            timeout=120,
            env=_scrubbed_env(pkgroot),
        )
        assert proc.returncode == 0, proc.stderr[-500:]
        lines = dict(
            line.split("=", 1) for line in proc.stdout.strip().splitlines()
        )
        assert lines["saved"] == "True"
        assert lines["path"] == str(_canonical(pkgroot))
        assert lines["back"] == "Shadow"
    created = sorted(pkgroot.rglob("bot_config.json"))
    assert created == [pkgroot / "bot_config.json", _canonical(pkgroot)]
    assert (pkgroot / "bot_config.json").read_bytes() == root_before
    data = json.loads(_canonical(pkgroot).read_text(encoding="utf-8"))
    assert data["agent_name"] == "Shadow"


def test_unknown_keys_round_trip(tmp_path):
    target = tmp_path / "bot.json"
    target.write_text(
        json.dumps({"mode": "llm_only", "mystery_key": "mval"}), encoding="utf-8"
    )
    cfg = BotConfig(config_file=target)
    assert cfg.get("mystery_key") == "mval"
    assert cfg.get_mode() == "llm_only"
    assert cfg.set_agent_stage_mode("single")
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["mystery_key"] == "mval"
    assert data["agent_stage_mode"] == "single"


def test_setter_validation_unchanged(tmp_path):
    cfg = BotConfig(config_file=tmp_path / "v.json")
    assert cfg.set_preferred_llm_model("not-a-model") is False
    assert not (tmp_path / "v.json").exists()
    assert cfg.set_preferred_llm_model("gpt-4o") is True


def test_local_llm_reads_singleton_config(tmp_path, monkeypatch):
    from core import local_llm

    target = tmp_path / "bot.json"
    target.write_text(
        json.dumps(
            {
                "preferred_ollama_model": "tm-plain",
                "preferred_tools_ollama_model": "tm-tools",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(core_config_mod, "config", BotConfig(config_file=target))
    monkeypatch.setattr(local_llm, "is_llamacpp", lambda: False)
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    assert local_llm.resolve_local_model(None) == "tm-plain"
    assert local_llm.resolve_local_model(None, with_tools=True) == "tm-tools"


def test_query_tracker_reads_singleton_config(tmp_path, monkeypatch):
    from api.query_tracker import QueryTracker

    target = tmp_path / "bot.json"
    target.write_text(
        json.dumps({"preferred_llm_model": "gpt-4o", "mode": "llm_only"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(core_config_mod, "config", BotConfig(config_file=target))
    got = QueryTracker._get_agent_config(None)
    assert got["preferred_llm_model"] == "gpt-4o"
    assert got["mode"] == "llm_only"


def test_settings_routes_use_singleton_file(tmp_path, monkeypatch):
    from tests.test_http_authz import _auth_client, LAN

    target = tmp_path / "bot.json"
    target.write_text(
        json.dumps({"agent_stage_mode": "multi-lite"}), encoding="utf-8"
    )
    monkeypatch.setattr(core_config_mod, "config", BotConfig(config_file=target))
    ctx = _auth_client(tmp_path, monkeypatch)
    ctx["client"].set_cookie("session_token", ctx["token"])

    res = ctx["client"].get("/api/settings", environ_base=LAN)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["settings"]["agent_stage_mode"] == "multi-lite"

    res = ctx["client"].post(
        "/api/settings", json={"agent_stage_mode": "single"}, environ_base=LAN
    )
    assert res.status_code == 200, res.get_json()
    assert json.loads(target.read_text(encoding="utf-8"))["agent_stage_mode"] == (
        "single"
    )
    # Legacy root-style sibling the test never pointed at stays absent.
    assert not (tmp_path / "bot_config.json").exists()
