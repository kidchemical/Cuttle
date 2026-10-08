"""Experimental feature flags (generic, feature-agnostic).

One registry, one settings key, one resolver. Every experimental feature in
Cuttle is a row in :data:`FLAG_SPECS`; the settings tab, the CLI, and the
runtime gates all read from here so there is exactly one place to add,
rename, or delete a flag.

Storage is a single permissive key in ``settings.json``::

    "experimental_flags": { "<flag_id>": true|false }

An id absent from the stored dict falls back to the spec's ``default``, so a
new flag works with no settings migration. An id that is not in the registry
resolves to **False** — unknown flags are never accidentally live.

Kill switch: ``CUTTLE_EXPERIMENTAL=0`` disables every flag regardless of
stored state (mirrors ``CUTTLE_AGENT_STEER`` in ``api.agent_harness.steer``).

This module deliberately knows nothing about any specific feature. Removing
every experimental feature from Cuttle is: drop the rows here, delete the
feature packages, and the generic surface still works.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# settings.json key that owns every experimental toggle.
SETTINGS_KEY = "experimental_flags"

# Categories drive grouping in the Settings tab.
FLAG_CATEGORIES = ("fun", "agents", "chat", "ui", "power")

# Risk drives UI styling + warning copy. Nothing here changes enforcement.
FLAG_RISKS = ("low", "medium", "high")

_KILL_SWITCH_VALUES = ("0", "false", "off", "no")


class FlagSpec:
    """Declarative description of one experimental feature flag."""

    __slots__ = (
        "id",
        "label",
        "description",
        "default",
        "category",
        "risk",
        "since",
        "needs_restart",
        "context_bundle",
    )

    def __init__(
        self,
        id: str,
        label: str,
        description: str,
        *,
        default: bool = False,
        category: str = "fun",
        risk: str = "low",
        since: str = "",
        needs_restart: bool = False,
        context_bundle: bool = False,
    ) -> None:
        self.id = str(id or "").strip().lower()
        self.label = str(label or "")
        self.description = str(description or "")
        self.default = bool(default)
        self.category = category if category in FLAG_CATEGORIES else "fun"
        self.risk = risk if risk in FLAG_RISKS else "low"
        self.since = str(since or "")
        self.needs_restart = bool(needs_restart)
        self.context_bundle = bool(context_bundle)

    def to_dict(self, enabled: Optional[bool] = None) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "default": self.default,
            "category": self.category,
            "risk": self.risk,
            "since": self.since,
            "needs_restart": self.needs_restart,
            "context_bundle": self.context_bundle,
            "enabled": self.default if enabled is None else bool(enabled),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FlagSpec {self.id} default={self.default}>"


# ---------------------------------------------------------------------------
# THE REGISTRY
#
# Adding an experimental feature = one row here + code that calls
# ``api.experimental.is_enabled("<id>")``. Deleting an experimental feature =
# delete its row, then delete its package. Nothing else in this file knows
# about features.
# ---------------------------------------------------------------------------
FLAG_SPECS: Dict[str, FlagSpec] = {}


def register_flag(spec: FlagSpec) -> FlagSpec:
    """Add (or replace) a flag spec. Raises on a malformed/duplicate id."""
    if not spec.id:
        raise ValueError("flag id is required")
    if spec.id in FLAG_SPECS:
        raise ValueError(f"duplicate experimental flag id: {spec.id}")
    FLAG_SPECS[spec.id] = spec
    return spec


# Built-ins. Imported lazily at the bottom of this module so that registering
# an experimental feature never creates an import cycle back into `api`.
def _register_builtins() -> None:
    from api.experimental import features  # noqa: F401  (import side effect)


def get_flag(flag_id: Any) -> Optional[FlagSpec]:
    """Return the spec for ``flag_id``, or None when it is not registered."""
    return FLAG_SPECS.get(str(flag_id or "").strip().lower())


def all_flags() -> List[FlagSpec]:
    return list(FLAG_SPECS.values())


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------
def kill_switch_active() -> bool:
    """True when ``CUTTLE_EXPERIMENTAL`` forces every flag off."""
    import os

    return (os.getenv("CUTTLE_EXPERIMENTAL") or "").strip().lower() in _KILL_SWITCH_VALUES


def _stored_flags() -> Dict[str, Any]:
    try:
        from managers.settings_manager import get_settings_manager

        sm = get_settings_manager()
        if hasattr(sm, "reload"):
            sm.reload()  # local agent CLI writes must become visible in Flask
        raw = sm.get_setting(SETTINGS_KEY, None)
    except Exception:
        raw = None
    return raw if isinstance(raw, dict) else {}


def is_enabled(flag_id: Any) -> bool:
    """Resolve one flag: kill switch → stored value → spec default.

    An unregistered id is False, so a typo can never light up a feature.
    """
    spec = get_flag(flag_id)
    if spec is None:
        return False
    if kill_switch_active():
        return False
    stored = _stored_flags().get(spec.id)
    if isinstance(stored, bool):
        return stored
    return spec.default


def enabled_flags() -> Dict[str, bool]:
    """Every registered flag id → resolved value (for bulk client delivery)."""
    disabled = kill_switch_active()
    stored = {} if disabled else _stored_flags()
    return {spec.id: False if disabled else stored.get(spec.id) if isinstance(stored.get(spec.id), bool) else spec.default
            for spec in FLAG_SPECS.values()}


def flags_payload() -> Dict[str, Any]:
    """Wire shape for GET /api/experimental/flags."""
    resolved = enabled_flags()
    flags = [spec.to_dict(resolved.get(spec.id, spec.default)) for spec in FLAG_SPECS.values()]
    flags.sort(key=lambda f: (f["category"], f["label"]))
    return {
        "flags": flags,
        "kill_switch": kill_switch_active(),
        "categories": list(FLAG_CATEGORIES),
    }


def set_enabled(flag_id: Any, value: Any) -> Dict[str, Any]:
    """Persist one flag. Unknown ids raise ``ValueError``.

    The kill switch is intentionally NOT persisted here: it is an operator
    escape hatch, so toggling a flag back on while it is engaged is a no-op
    that reports the kill switch rather than lying.
    """
    spec = get_flag(flag_id)
    if spec is None:
        raise ValueError(f"unknown experimental flag: {flag_id}")
    if not isinstance(value, bool):
        raise ValueError("enabled must be a boolean")
    enabled = value

    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    def apply(current):
        # Read and change ONE override under the settings store's lock.
        # Preserve unknown ids too: an older process must not erase newer flags.
        updated = dict(current) if isinstance(current, dict) else {}
        if enabled == spec.default:
            updated.pop(spec.id, None)
        else:
            updated[spec.id] = enabled
        return updated

    if sm.update_setting(SETTINGS_KEY, apply) is False:
        raise OSError("Could not persist experimental flags")
    return spec.to_dict(is_enabled(spec.id))


def reset_all() -> List[Dict[str, Any]]:
    """Drop every stored override so all flags return to spec defaults."""
    from managers.settings_manager import get_settings_manager

    sm = get_settings_manager()
    if hasattr(sm, "reload"):
        sm.reload()
    if sm.set_setting(SETTINGS_KEY, {}) is False:
        raise OSError("Could not persist experimental flags")
    return [spec.to_dict(is_enabled(spec.id)) for spec in FLAG_SPECS.values()]


_register_builtins()
