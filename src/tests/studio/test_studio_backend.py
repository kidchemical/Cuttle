"""Studio backend (L0): experimental flag and manifest identity colours."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
AGENTS = REPO / "src" / "api" / "agent_harness" / "agents"
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_studio_flag_is_registered_default_off():
    from api.experimental.flags import all_flags, get_flag, is_enabled

    spec = get_flag("chat_studio_display")
    assert spec is not None
    assert spec.default is False
    assert spec.category == "chat"
    assert spec.risk == "low"
    assert spec.label
    assert any(f.id == "chat_studio_display" for f in all_flags())
    assert is_enabled("chat_studio_display") is False


def test_every_bundled_manifest_has_a_valid_colour():
    manifests = sorted(AGENTS.glob("*/manifest.yaml"))
    assert manifests
    for path in manifests:
        data = yaml.safe_load(_read(path))
        color = data.get("color")
        assert isinstance(color, str) and HEX.match(color), (path.parent.name, color)


def test_public_catalog_exposes_each_manifest_colour():
    from api.agent_harness.catalog import public_catalog

    rows = {row["id"]: row for row in public_catalog()}
    for path in sorted(AGENTS.glob("*/manifest.yaml")):
        data = yaml.safe_load(_read(path))
        assert rows[data["id"]]["color"] == data["color"].lower(), data["id"]


def test_mock_colours_are_the_brand_values_from_the_plan():
    from api.agent_harness.catalog import public_catalog

    rows = {row["id"]: row["color"] for row in public_catalog()}
    assert rows["codex"] == "#19c37d"
    assert rows["cursor"] == "#4c8dff"
    assert rows["muse"] == "#a855f7"
    assert rows["claude"] == "#d97757"


def test_colour_validator_accepts_only_six_digit_hex():
    from api.agent_harness.catalog import _identity_color

    assert _identity_color("#19C37D") == "#19c37d"
    for bad in ("red", "#abc", "#12345g", "#19c37d; background:url(x)", "", None, 123):
        assert _identity_color(bad) == "", bad


def test_manifest_loader_drops_an_invalid_colour():
    from api.agent_harness.catalog import _manifest_from_dict

    good = _manifest_from_dict({"id": "x", "label": "X", "slash": "/x", "color": "#00FF00"},
                               fallback_id="x", source="user")
    bad = _manifest_from_dict({"id": "y", "label": "Y", "slash": "/y", "color": "green"},
                              fallback_id="y", source="user")
    assert good.color == "#00ff00"
    assert bad.color == ""


def test_public_dict_carries_colour_and_defaults_empty():
    from api.agent_harness.types import AgentManifest

    assert AgentManifest(id="x", label="X", slash="/x").to_public_dict()["color"] == ""
    assert AgentManifest(id="x", label="X", slash="/x", color="#123456").to_public_dict()["color"] == "#123456"
