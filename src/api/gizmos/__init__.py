"""Gizmos — live UI objects that exist outside chat bubbles (experimental).

Widgets render inside an agent's chat bubble (charts, forms). Gizmos live in
the shell instead: docked on the Electron title bar or the blade bar, floating
over every Space, or popped out as an always-on-top desktop window. The first
type is ``usage_meter`` (plan budget left + next reset/unblock per agent).

Layers: :mod:`catalog` (types) → :mod:`service` (validation/placement) →
:mod:`store` (SQLite + revision). Transport is :mod:`routes`; agents use
``python -m api.gizmos``. Everything is gated on the ``gizmos`` flag.

Teardown: delete the ``gizmos`` row in ``api/experimental/features.py``, this
package, ``src/web/js/gizmos/`` + ``css/gizmos*.css`` + the two pages, their
``<script>`` lines in ``app_shell.html``, the ``nav-gizmos`` rail entry, the
blueprint block in ``web_chat_api``, and the pop-out IPC in ``electron/``.
"""

from __future__ import annotations

FLAG_ID = "gizmos"


def is_enabled() -> bool:
    from api.experimental import is_enabled as _flag

    return _flag(FLAG_ID)
