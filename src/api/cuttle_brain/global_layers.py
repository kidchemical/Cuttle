"""Per-project ``GLOBAL.ini``: which global layers a project uses.

``{project}/.cuttle/GLOBAL.ini`` (install-local twin at
``{project}/.cuttle/personal/GLOBAL.ini`` wins for this machine). Absent or
unparsable file → defaults (everything on, additive).

``[global]`` keys (all optional):

- ``rules``: ``append`` (default; global + project compile) |
  ``shadow`` (a same-basename project file *replaces* the global one) |
  ``off`` (project rules only).
- ``docs``: ``on`` (default) | ``off`` (skip the global docs inventory leg).
- ``actions``: ``on`` (default) | ``off`` (skip the global actions fallback leg).

Non-severable: ``00-safety.md`` (shared-infra + cross-project rules) always
compiles; a project ``00-safety.md`` can only append. Unknown keys/values and
parse errors fall back to defaults with a log warning — never silently drop
context.
"""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

GLOBAL_FILENAME = "GLOBAL.ini"
SAFETY_RULE_FILES = ("00-safety.md",)


@dataclass(frozen=True)
class GlobalLayers:
    rules_mode: str = "append"  # append | shadow | off
    docs: bool = True
    actions: bool = True


DEFAULTS = GlobalLayers()


def _as_bool(value: str, default: bool) -> bool:
    text = (value or "").strip().lower()
    if text in ("1", "yes", "true", "on"):
        return True
    if text in ("0", "no", "false", "off"):
        return False
    return default


def parse_global_ini(text: str) -> GlobalLayers:
    """Parse GLOBAL.ini text; anything unknown falls back to defaults."""
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text or "")
    except configparser.Error as exc:
        log.warning("GLOBAL.ini unparsable, using defaults: %s", exc)
        return DEFAULTS
    if not parser.has_section("global"):
        return DEFAULTS
    rules_mode = parser.get("global", "rules", fallback="append").strip().lower()
    if rules_mode not in ("append", "shadow", "off"):
        log.warning("GLOBAL.ini [global] rules=%r unknown, using append", rules_mode)
        rules_mode = "append"
    for key in parser.options("global"):
        if key not in ("rules", "docs", "actions"):
            log.warning("GLOBAL.ini [global] ignoring unknown key %r", key)
    return GlobalLayers(
        rules_mode=rules_mode,
        docs=_as_bool(parser.get("global", "docs", fallback="on"), True),
        actions=_as_bool(parser.get("global", "actions", fallback="on"), True),
    )


def load_global_layers_for_cuttle_dir(cuttle_dir: Optional[Path]) -> GlobalLayers:
    """Load GLOBAL.ini beside a ``.cuttle`` dir (personal twin wins)."""
    if cuttle_dir is None:
        return DEFAULTS
    from api.cuttle_brain.personal_overlay import resolve_cuttle_file

    cfg_path = resolve_cuttle_file(Path(cuttle_dir), GLOBAL_FILENAME)
    if cfg_path is None:
        return DEFAULTS
    try:
        text = cfg_path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULTS
    return parse_global_ini(text)


def load_global_layers(project_path: Optional[str] = None) -> GlobalLayers:
    """Load the primary project's GLOBAL.ini (defaults when none applies)."""
    if not project_path:
        return DEFAULTS
    # Lazy import: context_compiler imports this module at top level.
    from api.cuttle_brain.context_compiler import _cuttle_dirs

    try:
        dirs = _cuttle_dirs(project_path)
    except Exception:
        return DEFAULTS
    if not dirs:
        return DEFAULTS
    return load_global_layers_for_cuttle_dir(dirs[0])
