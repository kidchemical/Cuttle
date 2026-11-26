"""An installed (packaged) Client: judged by version, updated by the installer.

kcslaptop regression (2026-11-25): switching from the repo daemon to the
installed app left the checkout's rev in the host's merged worker meta, so the
host flagged the laptop ``needs_update`` forever and offered a git update no
packaged worker can claim.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SIDECAR = REPO / "electron" / "device-worker" / "cuttle_device_worker.py"


@pytest.fixture()
def worker_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_DB", str(tmp_path / "device_workers.db"))
    monkeypatch.setenv("CUTTLE_DEVICE_WORKERS_ENABLED", "1")
    import api.device_workers.store as store_mod

    store_mod._store = None
    yield store_mod.get_store()
    store_mod._store = None


def test_capabilities_report_packaged_install(tmp_path, monkeypatch):
    from api.device_workers import capabilities as caps

    monkeypatch.setenv("CUTTLE_REPO_ROOT", str(tmp_path))
    assert caps.is_checkout() is False
    (tmp_path / ".git").write_text("gitdir: elsewhere", encoding="utf-8")  # worktree form
    assert caps.is_checkout() is True


def test_packaged_meta_clears_a_stale_checkout_rev(worker_db, monkeypatch):
    from api.device_workers import capabilities as caps
    from api.device_workers import platform as plat
    from api.device_workers.worker_loop import _meta_from_ads

    monkeypatch.setattr(caps, "cuttle_version", lambda: "0.13.2")
    monkeypatch.setattr(caps, "cuttle_git_rev", lambda: "37ce51600000")
    monkeypatch.setattr(plat, "worker_id", lambda: "host")
    # The repo daemon registered this id first, with its checkout rev.
    worker_db.upsert_worker(
        worker_id="kcslaptop", hostname="laptop",
        capabilities={"cuttle_self_update": True},
        meta={"cuttle_version": "0.13.2", "cuttle_git_rev": "01c8adf60381", "boot_git_rev": "01c8adf60381"},
    )
    packaged_caps = {"cuttle_self_update": False, "install": "packaged"}
    meta = _meta_from_ads({"cuttle_version": "0.13.2", "cuttle_git_rev": "", "capabilities": packaged_caps})
    worker_db.upsert_worker(worker_id="kcslaptop", hostname="laptop", capabilities=packaged_caps, meta=meta)

    laptop = {w["worker_id"]: w for w in plat.list_workers()["workers"]}["kcslaptop"]
    assert laptop["cuttle_git_rev"] == ""
    assert laptop["needs_update"] is False
    # A version behind the host still shows, without a git update to offer.
    monkeypatch.setattr(caps, "cuttle_version", lambda: "0.13.3")
    laptop = {w["worker_id"]: w for w in plat.list_workers()["workers"]}["kcslaptop"]
    assert laptop["needs_update"] is True
    refused = plat.submit_self_update(target_worker_id="kcslaptop")
    assert refused["success"] is False and "release installer" in refused["error"]


def _sidecar():
    spec = importlib.util.spec_from_file_location("cuttle_sidecar_packaged", SIDECAR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stdlib_sidecar_reports_packaged_install(monkeypatch):
    mod = _sidecar()
    monkeypatch.setattr(mod, "_cuttle_git_rev", lambda: "")
    monkeypatch.setattr(mod, "_boot_git_rev", lambda: "")
    ads = mod.collect_ads()
    assert ads["capabilities"]["install"] == "packaged"
    assert ads["capabilities"]["cuttle_self_update"] is False
    meta = mod._worker_payload("kcslaptop", ads)["meta"]
    assert meta["cuttle_git_rev"] == "" and meta["boot_git_rev"] == ""
