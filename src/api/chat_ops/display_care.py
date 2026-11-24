"""Display care for supervision check-ins (System care setting ``display``).

Work that needs a visible display (a game window, GPU captures, UI tests) hangs
or measures wrong once the screen idles off. With the setting on, the host does
two things and the supervisor decides the rest:

* **Keeps the display awake.** While any live supervision with the setting on
  has a target that is not finished, this process holds one idle inhibitor
  (``wakepy``: D-Bus on Linux desktops, the native calls on Windows and macOS).
  ``sync`` is the whole lifecycle: the worker loop calls it, it reads the
  schedules and takes or drops the inhibitor. Nothing is stored: the inhibitor
  dies with this process and the next ``sync`` takes it again.
* **Says when the display is off anyway** (locked by hand, or no inhibitor could
  be taken). That wakes the supervisor once per episode, a working target
  included. The host never wakes a display itself.

The pure part (``wanted``, ``display_state``, ``section``) takes plain dicts;
``sync`` and ``read`` are the only functions that touch the machine and never
raise. ``wakepy`` is imported on first use: without it, or without a desktop
session, the check-in says the display is not being kept awake.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from api.chat_ops import brief

# Seconds between schedule reads by ``sync`` (the worker loop ticks every 2 s).
SYNC_SEC = 30.0
# A target in this state has nothing left that could need the display.
_FINISHED = ('idle',)
_LIVE = ('pending', 'dispatching')
_OFF_TEXT = {'off': 'powered down', 'locked': 'locked or showing the screen saver'}


# -- pure ----------------------------------------------------------------------


def wanted(schedules: Iterable[Mapping[str, Any]]) -> List[str]:
    """Ids of the supervision schedules that need the display kept awake: live
    (not paused), setting on, target not seen finished. A schedule that has not
    run its first check yet counts as working."""
    out = []
    for op in schedules or ():
        if op.get('state') not in _LIVE or not brief.system((op.get('payload') or {}).get('system'))['display']:
            continue
        seen = (op.get('result') or {}).get('last_seen') or {}
        if seen.get('state') not in _FINISHED:
            out.append(str(op.get('id')))
    return out


def display_state(reading: Optional[str], hold: Mapping[str, Any], state: str,
                  previous: Mapping[str, Any]) -> Dict[str, Any]:
    """``{off, unheld, alert, tracking}`` for one reading against the last check.
    ``reading``: ``'on'`` / ``'off'`` / ``'locked'`` / None (cannot tell);
    ``hold``: ``status()``; ``state``: the target's state."""
    working = state not in _FINISHED
    off = reading if working and reading in _OFF_TEXT else ''
    unheld = str(hold.get('error') or '') if working else ''
    return {'off': off, 'unheld': unheld,
            'alert': bool((off and not previous.get('display_alerted'))
                          or (unheld and not previous.get('display_unheld_alerted'))),
            'tracking': {'display_alerted': bool(off), 'display_unheld_alerted': bool(unheld)}}


def section(picture: Optional[Mapping[str, Any]]) -> List[str]:
    """Check-in lines for a picture from ``read`` ([] when there is nothing to say)."""
    picture = picture or {}
    lines: List[str] = []
    if picture.get('off'):
        lines += ['', f"**Display is off** ({_OFF_TEXT[picture['off']]}): work that needs a visible display "
                  '(a game or app window, GPU captures, UI tests) can hang or measure wrong until it is back. '
                  'The host cannot wake it. Unless Focus or a recent user message to the chat allows it to wake '
                  'the display itself, have it switch to work that needs no display, and tell the user the '
                  'display has to be turned on for the rest.']
    if picture.get('unheld'):
        lines += ['', f"**Display not kept awake:** the host could not take an idle inhibitor "
                  f"({picture['unheld']}), so the screen may turn off when the machine idles. Tell the user if "
                  'the work needs the display.']
    return lines


# -- effects -------------------------------------------------------------------


class _Hold:
    """One ``wakepy`` presenting mode, entered and left on its own thread (the
    mode is bound to the thread that entered it)."""

    def __init__(self):
        self.release = threading.Event()
        self.ready = threading.Event()
        self.method = ''
        self.error = ''
        self.thread = threading.Thread(target=self._run, name='cuttle-display-care', daemon=True)
        self.thread.start()
        self.ready.wait(10)

    def _run(self):
        try:
            from wakepy import keep
            with keep.presenting(on_fail='pass') as mode:
                if mode.active:
                    self.method = str(getattr(mode.method, 'name', '') or mode.method or 'unknown')
                else:
                    self.error = 'no supported method on this desktop session'
                self.ready.set()
                if mode.active:
                    self.release.wait()
        except ImportError:
            self.error = 'the wakepy package is not installed'
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'[:160]
        finally:
            self.ready.set()

    @property
    def held(self):
        return bool(self.method) and self.thread.is_alive()


_lock = threading.Lock()
_hold: Optional[_Hold] = None
_error = ''
_last_sync = 0.0


def _schedules() -> List[Dict[str, Any]]:
    from api.chat_ops import store
    return store.schedules()


def status() -> Dict[str, Any]:
    """``{held, method, error}``: whether this process holds the inhibitor now."""
    hold = _hold
    return {'held': bool(hold and hold.held), 'method': hold.method if hold else '', 'error': _error}


def apply(want: bool) -> Dict[str, Any]:
    """Take (``want``) or drop the inhibitor; a failed take is retried on the next call."""
    global _hold, _error
    with _lock:
        if want and not (_hold and _hold.held):
            _hold = _Hold()
            _error = '' if _hold.held else (_hold.error or 'the inhibitor was not taken')
            if _hold.held:
                print(f'[CHAT OPS] display care: keeping the display awake ({_hold.method})', flush=True)
        elif not want:
            if _hold and _hold.held:
                print('[CHAT OPS] display care: released', flush=True)
            if _hold:
                _hold.release.set()
            _hold, _error = None, ''
    return status()


def sync(now: Optional[float] = None, *, force: bool = False) -> Optional[Dict[str, Any]]:
    """Match the inhibitor to the schedules, at most every ``SYNC_SEC``. Returns
    the status when it ran, None when it was not time yet."""
    global _last_sync
    now = time.time() if now is None else now
    if not force and now - _last_sync < SYNC_SEC:
        return None
    _last_sync = now
    try:
        return apply(bool(wanted(_schedules())))
    except Exception as exc:
        print(f'[CHAT OPS] display care sync failed: {type(exc).__name__}', flush=True)
        return None


def _display() -> Optional[str]:
    """``'on'`` / ``'off'`` / ``'locked'``, or None when this desktop cannot be asked.
    Linux session bus only: the screen saver (lock or blank) and GNOME's monitor
    power state."""
    if not sys.platform.startswith('linux'):
        return None
    from jeepney import DBusAddress, MessageType, Properties, new_method_call
    from jeepney.io.blocking import open_dbus_connection

    def ask(conn, message):
        reply = conn.send_and_get_reply(message, timeout=2)
        return reply.body[0] if reply.header.message_type == MessageType.method_return else None

    answered = False
    conn = open_dbus_connection(bus='SESSION')
    try:
        saver = DBusAddress('/org/freedesktop/ScreenSaver', bus_name='org.freedesktop.ScreenSaver',
                            interface='org.freedesktop.ScreenSaver')
        mutter = DBusAddress('/org/gnome/Mutter/DisplayConfig', bus_name='org.gnome.Mutter.DisplayConfig',
                             interface='org.gnome.Mutter.DisplayConfig')
        for kind, message in (('locked', new_method_call(saver, 'GetActive')),
                              ('off', Properties(mutter).get('PowerSaveMode'))):
            try:
                value = ask(conn, message)
            except Exception:
                continue
            if value is None:
                continue
            answered = True
            if value[1] if isinstance(value, tuple) else value:
                return kind
    finally:
        conn.close()
    return 'on' if answered else None


def read(cfg: Mapping[str, Any], state: str, previous: Mapping[str, Any]) -> Dict[str, Any]:
    """One check's display picture: ``{off, unheld, alert, tracking, seen}`` (all
    empty with the setting off)."""
    if not brief.system(cfg.get('system'))['display']:
        return {'off': '', 'unheld': '', 'alert': False, 'tracking': {}, 'seen': {}}
    try:
        reading = _display()
    except Exception:
        reading = None
    out = display_state(reading, status(), state, previous)
    out['seen'] = {k: v for k, v in (('display_off', out['off']), ('display_unheld', bool(out['unheld']))) if v}
    return out
