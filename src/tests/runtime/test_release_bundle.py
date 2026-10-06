"""Release builds ship product files, never this install's state or overlays."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _resources():
    pkg = json.loads((REPO / "electron" / "package.json").read_text(encoding="utf-8"))
    return {r["from"]: r for r in pkg["build"]["extraResources"]}


def test_src_bundle_excludes_runtime_state_and_personal_settings():
    filters = set(_resources()["../src"]["filter"])
    for pattern in ("!data/**", "!settings.json", "!**/.env", "!**/*.db", "!**/output/**", "!**/logs/**"):
        assert pattern in filters, pattern


def test_global_config_ships_without_personal_overlay():
    res = _resources()["../.cuttle_global"]
    assert res["to"] == "app/.cuttle_global"
    assert "!personal/**" in res["filter"]
