"""Experimental feature flags for Cuttle.

Public surface (the only names callers should import)::

    from api.experimental import is_enabled, enabled_flags, flags_payload

See :mod:`api.experimental.flags` for the resolver contract and
:mod:`api.experimental.features` for the registry rows.
"""

from __future__ import annotations

from api.experimental.flags import (  # noqa: F401  (re-export)
    FLAG_CATEGORIES,
    FLAG_RISKS,
    FLAG_SPECS,
    SETTINGS_KEY,
    FlagSpec,
    all_flags,
    enabled_flags,
    flags_payload,
    get_flag,
    is_enabled,
    kill_switch_active,
    register_flag,
    reset_all,
    set_enabled,
)

__all__ = [
    "FLAG_CATEGORIES",
    "FLAG_RISKS",
    "FLAG_SPECS",
    "SETTINGS_KEY",
    "FlagSpec",
    "all_flags",
    "enabled_flags",
    "flags_payload",
    "get_flag",
    "is_enabled",
    "kill_switch_active",
    "register_flag",
    "reset_all",
    "set_enabled",
]
