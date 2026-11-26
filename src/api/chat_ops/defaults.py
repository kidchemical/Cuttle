"""Install-wide supervision defaults (Apps → Supervision).

What a new supervision starts from when nothing more specific says otherwise.
Precedence, most specific first: the value given for that supervision (dialog
field or ``chat_cli supervise`` flag) → the orchestrator playbook's supervisor
block → these defaults → ``BUILTIN``. Existing schedules keep what they stored:
a default only ever fills a value a new supervision did not give.

``normalize`` is pure. ``load`` / ``save`` keep the defaults in
``settings.json`` (``supervision_defaults``; the paused-schedule alert stays in
its own key, ``supervision_paused_alert_hours``, which the worker reads).
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

from api.chat_ops import brief

SCHEMA_VERSION = 1
KEY = 'supervision_defaults'
ALERT_KEY = 'supervision_paused_alert_hours'
EVERY_MIN, EVERY_MAX = 60, 86400
ALERT_MAX_HOURS = 24 * 30
BUILTIN: Dict[str, Any] = {'every': 900, 'agent': '', 'involve_user': brief.DEFAULT_INVOLVE,
                           'nudge': brief.DEFAULT_NUDGE, 'system': dict(brief.DEFAULT_SYSTEM),
                           'paused_alert_hours': 6}


def normalize(raw: Any) -> Dict[str, Any]:
    """Validated defaults from a stored or posted dict; a missing field is the
    built-in. Raises ``ValueError`` naming the bad field."""
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError('Supervision defaults must be an object')
    unknown = [k for k in raw if k not in BUILTIN and k != 'schema_version']
    if unknown:
        raise ValueError(f"Unknown supervision default: {', '.join(sorted(unknown))}")
    try:
        every = int(raw.get('every', BUILTIN['every']))
    except (TypeError, ValueError):
        raise ValueError('every must be a whole number of seconds') from None
    if not EVERY_MIN <= every <= EVERY_MAX:
        raise ValueError(f'every must be {EVERY_MIN}–{EVERY_MAX} seconds')
    try:
        alert = float(raw.get('paused_alert_hours', BUILTIN['paused_alert_hours']))
    except (TypeError, ValueError):
        raise ValueError('paused_alert_hours must be a number') from None
    if not 0 <= alert <= ALERT_MAX_HOURS:
        raise ValueError(f'paused_alert_hours must be 0–{ALERT_MAX_HOURS} (0 turns the alert off)')
    agent = str(raw.get('agent') or '').strip().lstrip('/')
    if len(agent) > 64 or not all(c.isalnum() or c in '-_.' for c in agent):
        raise ValueError('agent must be a harness id')
    system = raw.get('system')
    return {'every': every, 'agent': agent, 'involve_user': brief.involve(raw.get('involve_user')),
            'nudge': brief.nudge(raw.get('nudge')),
            'system': brief.system(BUILTIN['system'] if system in (None, '') else system),
            'paused_alert_hours': int(alert) if alert == int(alert) else alert}


def _settings():
    from managers.settings_manager import get_settings_manager
    return get_settings_manager()


def load() -> Dict[str, Any]:
    """The defaults in force. Never raises: an unreadable or invalid store is the built-ins."""
    try:
        settings = _settings()
        stored = settings.get_setting(KEY) or {}
        return normalize({**stored, 'paused_alert_hours': settings.get_setting(
            ALERT_KEY, BUILTIN['paused_alert_hours'])})
    except Exception:
        return normalize(None)


def save(raw: Any) -> Dict[str, Any]:
    """Validate and store ``raw`` (a full or partial set: a missing field keeps
    its current value). Returns the defaults now in force."""
    if not isinstance(raw, Mapping):
        raise ValueError('Supervision defaults must be an object')
    new = normalize({**load(), **raw})
    settings = _settings()
    body = {k: v for k, v in new.items() if k != 'paused_alert_hours'}
    if not (settings.set_setting(KEY, {'schema_version': SCHEMA_VERSION, **body})
            and settings.set_setting(ALERT_KEY, new['paused_alert_hours'])):
        raise ValueError('Could not save the supervision defaults')
    return new


def view() -> Dict[str, Any]:
    return {'schema_version': SCHEMA_VERSION, 'defaults': load(), 'builtin': normalize(None)}
