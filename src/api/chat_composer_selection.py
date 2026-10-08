"""Shared next-turn agent selection; running-turn badges have a separate lifetime."""
from __future__ import annotations

from api.starred_slash import normalize_sticky_prefix


def normalize_selection(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("composer selection must be an object")
    chips = value.get("stickyChips", [])
    if not isinstance(chips, list) or len(chips) > 1:
        raise ValueError("composer selection accepts one agent chip")
    result = []
    for chip in chips:
        if not isinstance(chip, dict):
            raise ValueError("invalid agent chip")
        prefix = normalize_sticky_prefix(chip.get("prefix"))
        if not prefix:
            raise ValueError("invalid agent prefix")
        result.append({"prefix": prefix,
                       "label": str(chip.get("label") or prefix)[:200],
                       "category": str(chip.get("category") or prefix[1:])[:64]})
    return {"stickyChips": result, "stickyCleared": bool(value.get("stickyCleared")) and not result}


def selection_from_message(metadata: dict | None) -> dict | None:
    meta = metadata if isinstance(metadata, dict) else {}
    if meta.get("steered") or meta.get("speaker_kind") == "parent":
        return None
    sc = meta.get("slash_command") or {}
    chips = sc.get("chips") if isinstance(sc, dict) else None
    if not isinstance(chips, list) or not chips or not isinstance(chips[0], dict):
        return None
    chip = chips[0]
    prefix = str(chip.get("meta") or "").split(" ")[0]
    try:
        return normalize_selection({"stickyChips": [{**chip, "prefix": prefix}]})
    except ValueError:
        return None
