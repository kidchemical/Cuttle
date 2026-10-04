"""
Home automation presets (Govee) — themes, time-of-day schedule, auto-apply state.

Schedule file: src/data/home_automation/schedule.json
Auto state:    src/data/home_automation/auto_state.json
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from core.runtime_paths import home_automation_path, runtime_state_path

_SRC = Path(__file__).resolve().parent.parent
DATA_DIR = runtime_state_path("home_automation", project_root=_SRC.parent)
SCHEDULE_PATH = home_automation_path("schedule.json", _SRC.parent)
AUTO_STATE_PATH = home_automation_path("auto_state.json", _SRC.parent)
HEARTBEAT_PATH = home_automation_path("daemon_heartbeat.json", _SRC.parent)
DEVICES_PATH = home_automation_path("devices.json", _SRC.parent)
DEVICES_EXAMPLE_PATH = DATA_DIR / "devices.example.json"

# Must match ``run_home_automation_loop`` in cuttle_daemon.py (single source for UI countdown).
DAEMON_SCHEDULE_CHECK_INTERVAL_SEC = 60
DAEMON_SCHEDULE_STARTUP_DELAY_SEC = 90
# After a failed scheduled theme apply, wait this long before retrying the same period
# (avoids hammering Govee + holding govee_api_batch_lock every minute when devices 400).
DAEMON_SCHEDULE_FAILURE_BACKOFF_SEC = 15 * 60

# Pace v1 control calls; v2 scene steps keep GOVEE_V2_SCENE_GAP_SEC between H6008 scene applies.
GOVEE_V1_SPACING_SEC = 0.45
GOVEE_V2_SCENE_GAP_SEC = 7.5

# One burst at a time (theme apply vs multi-device status) across Flask and the Cuttle daemon.
GOVEE_API_BATCH_LOCK_PATH = home_automation_path("govee_api_batch.lock", _SRC.parent)
# Status polls should not sit on the lock for minutes while a theme apply runs.
GOVEE_STATUS_LOCK_WAIT_SEC = 8.0


def _try_remove_stale_govee_lock(path: Path, stale_seconds: float) -> None:
    if not path.exists():
        return
    try:
        age = time.time() - path.stat().st_mtime
        if age > stale_seconds:
            path.unlink(missing_ok=True)
            return
        txt = path.read_text(encoding="utf-8", errors="replace").strip().split()
        pid = int(txt[0]) if txt else 0
        if pid:
            import psutil

            if not psutil.pid_exists(pid):
                path.unlink(missing_ok=True)
    except (ValueError, OSError, IndexError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@contextlib.contextmanager
def govee_api_batch_lock(wait_seconds: float = 300.0, stale_seconds: float = 720.0) -> Iterator[None]:
    """Serialize heavy Govee usage so the home page status poll does not interleave with theme apply."""
    _ensure_data_dir()
    deadline = time.time() + wait_seconds
    path = GOVEE_API_BATCH_LOCK_PATH
    acquired = False
    while time.time() < deadline:
        _try_remove_stale_govee_lock(path, stale_seconds)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(f"{os.getpid()}\n")
            acquired = True
            break
        except FileExistsError:
            time.sleep(0.28)
    if not acquired:
        raise TimeoutError(
            "Timed out waiting for the Govee API lock (another theme apply or device status refresh is running)."
        )
    try:
        yield
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def load_govee_devices() -> List[Dict[str, str]]:
    """Local device inventory (gitignored). Empty until the user copies the example or runs discovery."""
    path = DEVICES_PATH if DEVICES_PATH.is_file() else None
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        device_id = str(row.get("device_id") or "").strip()
        model = str(row.get("model") or "").strip()
        if not device_id or not model:
            continue
        out.append(
            {
                "device_id": device_id,
                "model": model,
                "name": str(row.get("name") or device_id).strip(),
                "zone": str(row.get("zone") or "").strip(),
            }
        )
    return out


# Prefer load_govee_devices() / refresh_govee_devices(); mutated in place so importers stay current.
GOVEE_DEVICES: List[Dict[str, str]] = []


def refresh_govee_devices() -> List[Dict[str, str]]:
    GOVEE_DEVICES.clear()
    GOVEE_DEVICES.extend(load_govee_devices())
    return GOVEE_DEVICES


refresh_govee_devices()

THEME_QUICK: List[Dict[str, str]] = [
    {"id": "off", "label": "All off", "icon": "🌑", "description": "Turn every strip off"},
    {"id": "firelit", "label": "Firelit", "icon": "🔥", "description": "Candlelight on H6008 + flame orange on desk strips"},
    {"id": "cinematic", "label": "Cinematic", "icon": "🎬", "description": "1% deep purple — movie bias light"},
    {"id": "warm", "label": "Warm", "icon": "🕯️", "description": "Warm scene on H6008, 2700K on H6003"},
    {"id": "aurora", "label": "Aurora", "icon": "🌌", "description": "Aurora scene on H6008, cosmic purple on H6003"},
]

PERIOD_ORDER = ("midnight", "morning", "noon", "evening", "night")


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _reassert_theme_minutes(schedule: Dict[str, Any]) -> int:
    """Re-run apply_theme while period+theme unchanged, at least this many minutes apart (0 = never, old behavior)."""
    raw = schedule.get("reassertThemeMinutes")
    if raw is None:
        return 15
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 15
    return max(0, min(n, 24 * 60))


def _seconds_since_iso_timestamp(last_iso: Optional[str], now) -> Optional[float]:
    """``now`` is ``datetime.now()`` (local). ``last_iso`` from ``isoformat()`` on the same clock."""
    from datetime import datetime, timezone

    if not last_iso:
        return None
    try:
        s = str(last_iso).strip()
        la = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if la.tzinfo is not None:
            now_cmp = datetime.now(timezone.utc)
            return (now_cmp - la.astimezone(timezone.utc)).total_seconds()
        return (now - la).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None


def default_schedule() -> Dict[str, Any]:
    return {
        "version": 1,
        # Opt-in: daemon must not start applying themes until the user enables it.
        "autoApplyEnabled": False,
        "reassertThemeMinutes": 15,
        "periods": {
            "midnight": {"start": "00:00", "end": "06:00", "theme": "off"},
            "morning": {"start": "06:00", "end": "10:00", "theme": "off"},
            "noon": {"start": "10:00", "end": "16:00", "theme": "off"},
            "evening": {"start": "16:00", "end": "21:00", "theme": "firelit"},
            "night": {"start": "21:00", "end": "24:00", "theme": "cinematic"},
        },
    }


def _parse_hhmm(s: str) -> int:
    parts = s.strip().split(":")
    h = int(parts[0])
    m = int(parts[1]) if len(parts) > 1 else 0
    return h * 60 + m


def _minutes_in_period(mins: int, start: int, end: int) -> bool:
    """Half-open [start, end) in minutes 0..1440; end 1440 = midnight next day."""
    if end <= 1440 and start < end:
        return start <= mins < end
    if end > 1440:
        return mins >= start or mins < (end - 1440)
    return False


def load_schedule() -> Dict[str, Any]:
    _ensure_data_dir()
    if not SCHEDULE_PATH.exists():
        data = default_schedule()
        save_schedule(data)
        return data
    try:
        with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "periods" not in data:
            return default_schedule()
        return data
    except Exception:
        return default_schedule()


def save_schedule(data: Dict[str, Any]) -> None:
    _ensure_data_dir()
    with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def current_period_for_schedule(schedule: Dict[str, Any], hour: int, minute: int) -> Optional[str]:
    mins = hour * 60 + minute
    periods = schedule.get("periods") or {}
    for key in PERIOD_ORDER:
        cfg = periods.get(key)
        if not isinstance(cfg, dict):
            continue
        start = _parse_hhmm(str(cfg.get("start", "0:00")))
        end_s = str(cfg.get("end", "24:00"))
        if end_s in ("24:00", "24:0"):
            end = 1440
        else:
            end = _parse_hhmm(end_s)
        if end == 0 and end_s == "24:00":
            end = 1440
        if _minutes_in_period(mins, start, end):
            return key
    return None


def theme_for_period(schedule: Dict[str, Any], period_key: str) -> str:
    periods = schedule.get("periods") or {}
    cfg = periods.get(period_key) or {}
    return str(cfg.get("theme", "off"))


def _append(lines: List[str], errors: List[str], ok: bool, msg: str) -> bool:
    lines.append(msg)
    if not ok:
        errors.append(msg)
    return ok


def _note_rate_limit(meta: Dict[str, Any], exc: BaseException) -> None:
    code = getattr(exc, "status_code", None)
    if code == 429:
        meta["rateLimited"] = True
    ra = getattr(exc, "retry_after_seconds", None)
    if ra is not None:
        cur = meta.get("retryAfterSeconds")
        meta["retryAfterSeconds"] = int(ra) if cur is None else max(cur, int(ra))
    elif code == 429 and meta.get("retryAfterSeconds") is None:
        meta["retryAfterSeconds"] = 60
        meta["retryAfterEstimate"] = True
        meta["rateLimited"] = True


def _build_apply_theme_result(
    ok_all: bool,
    tid: str,
    lines: List[str],
    errors: List[str],
    rate_meta: Dict[str, Any],
) -> Dict[str, Any]:
    from datetime import datetime, timedelta, timezone

    out: Dict[str, Any] = {"success": ok_all, "theme": tid, "log": lines, "errors": errors}
    if rate_meta.get("rateLimited"):
        out["rateLimited"] = True
        ra = rate_meta.get("retryAfterSeconds")
        if ra is not None:
            out["retryAfterSeconds"] = int(ra)
            out["retryAfterAt"] = (
                datetime.now(timezone.utc) + timedelta(seconds=int(ra))
            ).replace(microsecond=0).isoformat()
        if rate_meta.get("retryAfterEstimate"):
            out["retryAfterEstimate"] = True
    return out


def _theme_apply_progress(cb: Optional[Callable[[Dict[str, Any]], None]], **kwargs: Any) -> None:
    if cb:
        cb(dict(kwargs))


def _govee_targets_from_api() -> List[Dict[str, str]]:
    """All controllable lights returned by Govee /devices (API key's account). Empty if list fails."""
    from tools.govee.govee_api import GoveeError, govee_list_devices

    try:
        listed = govee_list_devices()
    except GoveeError as e:
        if getattr(e, "status_code", None) == 429:
            raise
        return []
    out: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in listed:
        if item.get("controllable") is False:
            continue
        did = item.get("device_id")
        mod = item.get("model")
        if not did or not mod:
            continue
        key = (str(did), str(mod))
        if key in seen:
            continue
        seen.add(key)
        name = str(item.get("name") or did)
        out.append({"device_id": str(did), "model": str(mod), "name": name})
    return out


def _resolved_govee_devices() -> List[Dict[str, str]]:
    """Configured catalog intersected with the API account list (skips re-paired / wrong-key IDs)."""
    refresh_govee_devices()
    zone_by_id = {d["device_id"].lower(): d.get("zone") for d in GOVEE_DEVICES}
    name_by_id = {d["device_id"].lower(): d.get("name") for d in GOVEE_DEVICES}
    try:
        api = _govee_targets_from_api()
    except Exception:
        return list(GOVEE_DEVICES)
    if not api:
        return list(GOVEE_DEVICES)
    api_by_id = {str(d["device_id"]).lower(): d for d in api}
    matched: List[Dict[str, str]] = []
    for d in GOVEE_DEVICES:
        key = d["device_id"].lower()
        if key not in api_by_id:
            continue
        row = dict(d)
        # Prefer Govee app name when present
        api_name = api_by_id[key].get("name")
        if api_name:
            row["name"] = str(api_name)
        matched.append(row)
    if matched:
        return matched
    # None of our catalog IDs are on this key — use live list so themes still do something.
    out: List[Dict[str, str]] = []
    for d in api:
        key = str(d["device_id"]).lower()
        out.append(
            {
                "device_id": str(d["device_id"]),
                "model": str(d["model"]),
                "name": name_by_id.get(key) or str(d.get("name") or d["device_id"]),
                "zone": zone_by_id.get(key) or "",
            }
        )
    return out or list(GOVEE_DEVICES)


def _apply_theme_impl(
    theme_id: str,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    from tools.govee.govee_api import (
        GoveeError,
        govee_set_brightness,
        govee_set_color,
        govee_set_color_temperature,
        govee_set_power,
    )
    from tools.govee.govee_v2 import GoveeV2Error, govee_v2_apply_light_scene_value, govee_v2_find_light_scene_value

    lines: List[str] = []
    errors: List[str] = []
    rate_meta: Dict[str, Any] = {}
    tid = (theme_id or "").strip().lower()

    def _dev_id(d: Dict[str, str]) -> str:
        return str(d["device_id"]).lower()

    def _p(**kwargs: Any) -> None:
        _theme_apply_progress(progress_cb, **kwargs)

    def _retry_cb(did: str, name: str, detail: str):
        def inner(ra: int, attempt: int):
            _p(
                type="device",
                device_id=did,
                name=name,
                phase="retry",
                detail=detail,
                retryAfter=ra,
                attempt=attempt,
            )

        return inner

    def devs(model: Optional[str] = None):
        catalog = _resolved_govee_devices()
        if model:
            return [d for d in catalog if d["model"] == model]
        return list(catalog)

    ok_all = True

    try:
        if tid == "off":
            # Use API device list so every light on the account turns off, not only GOVEE_DEVICES.
            _p(type="phase", message="Loading device list…")
            try:
                targets = _govee_targets_from_api()
            except GoveeError as e:
                ok_all = False
                _append(lines, errors, False, str(e))
                _note_rate_limit(rate_meta, e)
                _p(type="theme_complete", success=False)
                return _build_apply_theme_result(ok_all, tid, lines, errors, rate_meta)
            if not targets:
                targets = list(GOVEE_DEVICES)
            for d in targets:
                dn = str(d.get("name") or d["device_id"])
                did = _dev_id(d)
                det = "Turn off"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                try:
                    govee_set_power(d["device_id"], d["model"], "off", on_retry=_retry_cb(did, dn, det))
                    _append(lines, errors, True, f"{dn}: off")
                    _p(type="device", device_id=did, name=dn, phase="done", detail="Off")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, f"{dn}: {e}")
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="Off", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)

        elif tid == "cinematic":
            for d in devs():
                dn, did = d["name"], _dev_id(d)
                det = "Cinematic (on + color + 1%)"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                try:
                    cb = _retry_cb(did, dn, det)
                    govee_set_power(d["device_id"], d["model"], "on", on_retry=cb)
                    govee_set_color(d["device_id"], d["model"], 48, 6, 82, on_retry=cb)
                    govee_set_brightness(d["device_id"], d["model"], 1, on_retry=cb)
                    _append(lines, errors, True, f"{dn}: cinematic 1% purple")
                    _p(type="device", device_id=did, name=dn, phase="done", detail="Cinematic")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, f"{dn}: {e}")
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="Cinematic", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)

        elif tid == "firelit":
            h8 = devs("H6008")
            h3 = devs("H6003")
            scene_val = None
            _p(type="phase", message="Resolving Candlelight scene…")
            try:
                if not h8:
                    raise GoveeV2Error("No H6008 devices on this Govee account match the catalog")
                probe = h8[0]
                scene_val = govee_v2_find_light_scene_value(
                    probe["device_id"], probe["model"], "Candlelight"
                )
            except GoveeV2Error as e:
                ok_all = False
                lines.append(f"Candlelight lookup failed: {e}")
                errors.append(str(e))
                _note_rate_limit(rate_meta, e)

            for d in devs():
                dn, did = d["name"], _dev_id(d)
                det = "Power on"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                try:
                    govee_set_power(d["device_id"], d["model"], "on", on_retry=_retry_cb(did, dn, det))
                    _p(type="device", device_id=did, name=dn, phase="done", detail="Power on")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, f"{dn} power: {e}")
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="Power on", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)

            if scene_val:
                for i, d in enumerate(h8):
                    dn, did = d["name"], _dev_id(d)
                    det = "Candlelight scene"
                    _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                    try:
                        govee_v2_apply_light_scene_value(
                            d["device_id"], d["model"], scene_val, on_retry=_retry_cb(did, dn, det)
                        )
                        _append(lines, errors, True, f"{dn}: Candlelight scene")
                        _p(type="device", device_id=did, name=dn, phase="done", detail="Candlelight scene")
                    except GoveeV2Error as e:
                        ok_all = False
                        _append(lines, errors, False, f"{dn} scene: {e}")
                        _note_rate_limit(rate_meta, e)
                        _p(type="device", device_id=did, name=dn, phase="error", detail="Candlelight scene", message=str(e))
                    if i < len(h8) - 1:
                        time.sleep(GOVEE_V2_SCENE_GAP_SEC)
                for d in h8:
                    dn, did = d["name"], _dev_id(d)
                    det = "Brightness 50%"
                    _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                    try:
                        govee_set_brightness(
                            d["device_id"], d["model"], 50, on_retry=_retry_cb(did, dn, det)
                        )
                        _p(type="device", device_id=did, name=dn, phase="done", detail="Brightness 50%")
                    except GoveeError as e:
                        ok_all = False
                        _append(lines, errors, False, f"{dn} brightness: {e}")
                        _note_rate_limit(rate_meta, e)
                        _p(type="device", device_id=did, name=dn, phase="error", detail="Brightness", message=str(e))
                    time.sleep(GOVEE_V1_SPACING_SEC)

            flame = ((255, 55, 8), (255, 72, 12))
            for idx, d in enumerate(h3):
                dn, did = d["name"], _dev_id(d)
                det = "Flame RGB + 50%"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                r, g, b = flame[min(idx, len(flame) - 1)]
                try:
                    cb = _retry_cb(did, dn, det)
                    govee_set_color(d["device_id"], d["model"], r, g, b, on_retry=cb)
                    govee_set_brightness(d["device_id"], d["model"], 50, on_retry=cb)
                    _append(lines, errors, True, f"{dn}: flame RGB + 50%")
                    _p(type="device", device_id=did, name=dn, phase="done", detail="Flame")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, f"{dn}: {e}")
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="Flame", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)

        elif tid == "warm":
            _p(type="phase", message="Resolving Warm scene…")
            warm_val = None
            try:
                h8w = devs("H6008")
                if not h8w:
                    raise GoveeV2Error("No H6008 devices available for Warm scene")
                probe = h8w[0]
                warm_val = govee_v2_find_light_scene_value(probe["device_id"], probe["model"], "Warm")
            except GoveeV2Error as e:
                warm_val = None
                lines.append(f"Warm scene unavailable: {e}")
                _note_rate_limit(rate_meta, e)
            for d in devs():
                dn, did = d["name"], _dev_id(d)
                det = "Power on"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                try:
                    govee_set_power(d["device_id"], d["model"], "on", on_retry=_retry_cb(did, dn, det))
                    _p(type="device", device_id=did, name=dn, phase="done", detail="Power on")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, str(e))
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="Power on", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)
            if warm_val:
                h8 = devs("H6008")
                for i, d in enumerate(h8):
                    dn, did = d["name"], _dev_id(d)
                    det = "Warm scene"
                    _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                    try:
                        govee_v2_apply_light_scene_value(
                            d["device_id"], d["model"], warm_val, on_retry=_retry_cb(did, dn, det)
                        )
                        _append(lines, errors, True, f"{dn}: Warm scene")
                        _p(type="device", device_id=did, name=dn, phase="done", detail="Warm scene")
                    except GoveeV2Error as e:
                        ok_all = False
                        _append(lines, errors, False, str(e))
                        _note_rate_limit(rate_meta, e)
                        _p(type="device", device_id=did, name=dn, phase="error", detail="Warm scene", message=str(e))
                    if i < len(h8) - 1:
                        time.sleep(GOVEE_V2_SCENE_GAP_SEC)
                for d in h8:
                    dn, did = d["name"], _dev_id(d)
                    det = "Brightness 55%"
                    _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                    try:
                        govee_set_brightness(
                            d["device_id"], d["model"], 55, on_retry=_retry_cb(did, dn, det)
                        )
                        _p(type="device", device_id=did, name=dn, phase="done", detail="Brightness 55%")
                    except GoveeError as e:
                        _note_rate_limit(rate_meta, e)
                        _p(type="device", device_id=did, name=dn, phase="error", detail="Brightness", message=str(e))
                    time.sleep(GOVEE_V1_SPACING_SEC)
            for d in devs("H6003"):
                dn, did = d["name"], _dev_id(d)
                det = "2700K + 55%"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                try:
                    cb = _retry_cb(did, dn, det)
                    govee_set_color_temperature(d["device_id"], d["model"], 2700, on_retry=cb)
                    govee_set_brightness(d["device_id"], d["model"], 55, on_retry=cb)
                    _append(lines, errors, True, f"{dn}: 2700K")
                    _p(type="device", device_id=did, name=dn, phase="done", detail="2700K")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, str(e))
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="2700K", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)

        elif tid == "aurora":
            _p(type="phase", message="Resolving Aurora scene…")
            aurora_val = None
            try:
                h8a = devs("H6008")
                if not h8a:
                    raise GoveeV2Error("No H6008 devices available for Aurora scene")
                probe = h8a[0]
                aurora_val = govee_v2_find_light_scene_value(probe["device_id"], probe["model"], "Aurora")
            except GoveeV2Error as e:
                aurora_val = None
                lines.append(f"Aurora scene: {e}")
                _note_rate_limit(rate_meta, e)
            for d in devs():
                dn, did = d["name"], _dev_id(d)
                det = "Power on"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                try:
                    govee_set_power(d["device_id"], d["model"], "on", on_retry=_retry_cb(did, dn, det))
                    _p(type="device", device_id=did, name=dn, phase="done", detail="Power on")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, str(e))
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="Power on", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)
            if aurora_val:
                h8 = devs("H6008")
                for i, d in enumerate(h8):
                    dn, did = d["name"], _dev_id(d)
                    det = "Aurora scene"
                    _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                    try:
                        govee_v2_apply_light_scene_value(
                            d["device_id"], d["model"], aurora_val, on_retry=_retry_cb(did, dn, det)
                        )
                        _append(lines, errors, True, f"{dn}: Aurora")
                        _p(type="device", device_id=did, name=dn, phase="done", detail="Aurora scene")
                    except GoveeV2Error as e:
                        ok_all = False
                        _append(lines, errors, False, str(e))
                        _note_rate_limit(rate_meta, e)
                        _p(type="device", device_id=did, name=dn, phase="error", detail="Aurora scene", message=str(e))
                    if i < len(h8) - 1:
                        time.sleep(GOVEE_V2_SCENE_GAP_SEC)
                for d in h8:
                    dn, did = d["name"], _dev_id(d)
                    det = "Brightness 85%"
                    _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                    try:
                        govee_set_brightness(
                            d["device_id"], d["model"], 85, on_retry=_retry_cb(did, dn, det)
                        )
                        _p(type="device", device_id=did, name=dn, phase="done", detail="Brightness 85%")
                    except GoveeError as e:
                        _note_rate_limit(rate_meta, e)
                        _p(type="device", device_id=did, name=dn, phase="error", detail="Brightness", message=str(e))
                    time.sleep(GOVEE_V1_SPACING_SEC)
            cols = ((45, 25, 120), (110, 40, 160))
            for idx, d in enumerate(devs("H6003")):
                dn, did = d["name"], _dev_id(d)
                det = "Cosmic RGB + 85%"
                _p(type="device", device_id=did, name=dn, phase="running", detail=det)
                r, g, b = cols[min(idx, 1)]
                try:
                    cb = _retry_cb(did, dn, det)
                    govee_set_color(d["device_id"], d["model"], r, g, b, on_retry=cb)
                    govee_set_brightness(d["device_id"], d["model"], 85, on_retry=cb)
                    _append(lines, errors, True, f"{dn}: cosmic RGB")
                    _p(type="device", device_id=did, name=dn, phase="done", detail="Cosmic RGB")
                except GoveeError as e:
                    ok_all = False
                    _append(lines, errors, False, str(e))
                    _note_rate_limit(rate_meta, e)
                    _p(type="device", device_id=did, name=dn, phase="error", detail="Cosmic RGB", message=str(e))
                time.sleep(GOVEE_V1_SPACING_SEC)

        else:
            _p(type="theme_complete", success=False)
            return {"success": False, "error": f"Unknown theme: {theme_id}", "log": [], "errors": [f"Unknown theme: {theme_id}"]}

    except Exception as e:
        ok_all = False
        errors.append(str(e))
        lines.append(str(e))
        if isinstance(e, (GoveeError, GoveeV2Error)):
            _note_rate_limit(rate_meta, e)

    _p(type="theme_complete", success=ok_all)
    return _build_apply_theme_result(ok_all, tid, lines, errors, rate_meta)


def iter_apply_theme_stream_events(theme_id: str) -> Iterator[Dict[str, Any]]:
    """Yield progress dicts for NDJSON streaming (Flask). Ends with ``{"type": "final", "result": ...}``."""
    import queue
    import threading
    import os

    out_q: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            if not (os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key")):
                out_q.put({"type": "final", "result": {"success": False, "error": "GOVEE_API_KEY is not set"}})
                return

            def progress(ev: Dict[str, Any]) -> None:
                out_q.put(ev)

            try:
                with govee_api_batch_lock():
                    result = _apply_theme_impl(theme_id, progress_cb=progress)
            except TimeoutError:
                out_q.put(
                    {
                        "type": "final",
                        "result": {
                            "success": False,
                            "errors": [
                                "Timed out waiting for the Govee API lock (another operation is running)."
                            ],
                        },
                    }
                )
                return

            out_q.put({"type": "final", "result": result})
        except Exception as e:
            out_q.put({"type": "final", "result": {"success": False, "error": str(e)}})
        finally:
            out_q.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while True:
        item = out_q.get()
        if item is None:
            break
        yield item
    thread.join(timeout=720.0)


def apply_theme(
    theme_id: str,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Apply a quick theme; serialized with device-status sweeps and retried HTTP on 429 inside govee_api."""
    tid = (theme_id or "").strip().lower()
    try:
        with govee_api_batch_lock():
            return _apply_theme_impl(theme_id, progress_cb=progress_cb)
    except TimeoutError:
        return {
            "success": False,
            "theme": tid,
            "log": [],
            "errors": [
                "Govee API is busy: another theme apply or the device list on the home automation page is running. "
                "Wait a few seconds and try Apply schedule now again."
            ],
        }


def _load_auto_state() -> Dict[str, Any]:
    if not AUTO_STATE_PATH.exists():
        return {}
    try:
        with open(AUTO_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_auto_state(state: Dict[str, Any]) -> None:
    _ensure_data_dir()
    with open(AUTO_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def record_daemon_schedule_tick() -> None:
    """Call from the daemon after each scheduled lighting check (before sleeping until the next)."""
    from datetime import datetime, timezone

    _ensure_data_dir()
    payload = {
        "lastTickAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "intervalSeconds": DAEMON_SCHEDULE_CHECK_INTERVAL_SEC,
    }
    try:
        with open(HEARTBEAT_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except OSError:
        pass


def read_daemon_schedule_heartbeat() -> Dict[str, Any]:
    if not HEARTBEAT_PATH.exists():
        return {}
    try:
        with open(HEARTBEAT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_schedule_daemon_heartbeat_status() -> Dict[str, Any]:
    """Server-side next-tick estimate from the last daemon heartbeat file."""
    from datetime import datetime, timedelta, timezone

    hb = read_daemon_schedule_heartbeat()
    interval = int(hb.get("intervalSeconds") or DAEMON_SCHEDULE_CHECK_INTERVAL_SEC)
    last_iso = hb.get("lastTickAt")
    now = datetime.now(timezone.utc)
    base = {
        "intervalSeconds": interval,
        "startupDelaySeconds": DAEMON_SCHEDULE_STARTUP_DELAY_SEC,
    }
    if not last_iso:
        return {
            **base,
            "hasHeartbeat": False,
            "hint": "No tick recorded yet. With the Cuttle daemon running, the first check runs after the startup delay, then about every "
            f"{interval} seconds.",
        }
    try:
        raw = str(last_iso).replace("Z", "+00:00")
        last = datetime.fromisoformat(raw)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {**base, "hasHeartbeat": False, "error": "invalid heartbeat timestamp"}
    next_tick = last + timedelta(seconds=interval)
    secs = (next_tick - now).total_seconds()
    stale_threshold = interval * 3 + 30
    overdue_by = (now - last).total_seconds()
    daemon_likely_running = overdue_by < stale_threshold
    return {
        **base,
        "hasHeartbeat": True,
        "lastTickAt": last_iso,
        "nextTickAt": next_tick.replace(microsecond=0).isoformat(),
        "secondsUntilNext": max(0, int(secs)),
        "secondsPastDue": int(-secs) if secs < 0 else 0,
        "daemonLikelyRunning": daemon_likely_running,
    }


def list_govee_devices_with_live_state() -> Dict[str, Any]:
    """Devices from Govee API plus per-device state (online, power, brightness)."""
    import os

    from tools.govee.govee_api import (
        GoveeError,
        flatten_govee_state_properties,
        govee_get_device_state,
        govee_list_devices,
    )

    if not (os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key")):
        return {"devices": [], "error": "no_govee_key"}

    zone_by_id = {d["device_id"].lower(): d.get("zone") for d in GOVEE_DEVICES}
    name_by_id = {d["device_id"].lower(): d.get("name") for d in GOVEE_DEVICES}

    try:
        with govee_api_batch_lock(wait_seconds=GOVEE_STATUS_LOCK_WAIT_SEC):
            try:
                listed = govee_list_devices()
            except GoveeError as e:
                return {"devices": [], "error": str(e), "rateLimited": getattr(e, "status_code", None) == 429}

            rows: List[Dict[str, Any]] = []
            for item in listed:
                if item.get("controllable") is False:
                    continue
                did = item.get("device_id")
                mod = item.get("model")
                if not did or not mod:
                    continue
                did_s, mod_s = str(did), str(mod)
                key_l = did_s.lower()
                row: Dict[str, Any] = {
                    "device_id": did_s,
                    "model": mod_s,
                    "name": name_by_id.get(key_l) or str(item.get("name") or did_s),
                    "zone": zone_by_id.get(key_l),
                    "controllable": item.get("controllable"),
                    "retrievable": item.get("retrievable"),
                }
                if item.get("retrievable") is False:
                    row["online"] = None
                    row["powerState"] = None
                    row["brightness"] = None
                    row["stateOk"] = False
                    row["stateError"] = "State not retrievable for this model (API)."
                    rows.append(row)
                    continue
                try:
                    raw = govee_get_device_state(did_s, mod_s)
                    props = flatten_govee_state_properties(raw.get("properties"))
                    row["stateOk"] = True
                    row["stateError"] = None
                    if "online" in props:
                        row["online"] = bool(props["online"])
                    else:
                        row["online"] = None
                    ps = props.get("powerState")
                    if ps is not None:
                        row["powerState"] = str(ps).lower() if isinstance(ps, str) else ps
                    else:
                        turn = props.get("turn")
                        row["powerState"] = str(turn).lower() if isinstance(turn, str) else None
                    br = props.get("brightness")
                    row["brightness"] = int(br) if isinstance(br, (int, float)) else None
                    if "colorTem" in props:
                        row["colorTemperature"] = props.get("colorTem")
                    if isinstance(props.get("color"), dict):
                        row["color"] = props.get("color")
                except GoveeError as e:
                    row["stateOk"] = False
                    row["stateError"] = str(e)
                    row["online"] = None
                    row["powerState"] = None
                    row["brightness"] = None
                rows.append(row)
                time.sleep(GOVEE_V1_SPACING_SEC)

            return {"devices": rows}
    except TimeoutError:
        return {
            "devices": [],
            "error": "Govee API is busy (a theme apply is running). Refresh again in a few seconds.",
            "busy": True,
        }


def _record_scheduled_apply_outcome(
    state: Dict[str, Any], period: str, theme: str, now, success: bool
) -> None:
    """Update auto-apply state after a scheduled theme run (daemon or manual)."""
    # Always stamp period/theme so failure backoff can match the same slot
    # (otherwise retries every DAEMON_SCHEDULE_CHECK_INTERVAL_SEC forever).
    state["last_period"] = period
    state["last_theme"] = theme
    if success:
        state["last_applied_at"] = now.isoformat()
        state["last_success"] = True
    else:
        state["last_success"] = False
        state["last_error_at"] = now.isoformat()
    _save_auto_state(state)


def apply_scheduled_theme_now() -> Dict[str, Any]:
    """Apply the theme for whatever schedule period matches the clock now. Ignores skip logic and ``autoApplyEnabled`` (for UI / tray)."""
    import os
    from datetime import datetime

    if not (os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key")):
        return {"success": False, "error": "GOVEE_API_KEY is not set"}

    schedule = load_schedule()
    now = datetime.now()
    period = current_period_for_schedule(schedule, now.hour, now.minute)
    if not period:
        return {"success": False, "error": "No schedule period matches the current time"}

    theme = theme_for_period(schedule, period)
    state = _load_auto_state()
    result = apply_theme(theme)
    _record_scheduled_apply_outcome(state, period, theme, now, bool(result.get("success")))
    result["period"] = period
    result["auto"] = False
    result["manualScheduleApply"] = True
    return result


def iter_apply_scheduled_theme_stream_events() -> Iterator[Dict[str, Any]]:
    """Yield progress dicts for NDJSON streaming (Flask). Ends with ``{"type": "final", "result": ...}``."""
    import queue
    import threading
    import os
    from datetime import datetime

    out_q: queue.Queue = queue.Queue()

    def worker() -> None:
        try:
            if not (os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key")):
                out_q.put({"type": "final", "result": {"success": False, "error": "GOVEE_API_KEY is not set"}})
                return
            schedule = load_schedule()
            now = datetime.now()
            period = current_period_for_schedule(schedule, now.hour, now.minute)
            if not period:
                out_q.put(
                    {
                        "type": "final",
                        "result": {"success": False, "error": "No schedule period matches the current time"},
                    }
                )
                return
            theme = theme_for_period(schedule, period)
            state = _load_auto_state()
            out_q.put({"type": "schedule_start", "period": period, "theme": theme})

            def progress(ev: Dict[str, Any]) -> None:
                out_q.put(ev)

            try:
                with govee_api_batch_lock():
                    result = _apply_theme_impl(theme, progress_cb=progress)
            except TimeoutError:
                out_q.put(
                    {
                        "type": "final",
                        "result": {
                            "success": False,
                            "errors": [
                                "Timed out waiting for the Govee API lock (another operation is running)."
                            ],
                        },
                    }
                )
                return

            _record_scheduled_apply_outcome(state, period, theme, now, bool(result.get("success")))
            result["period"] = period
            result["auto"] = False
            result["manualScheduleApply"] = True
            out_q.put({"type": "final", "result": result})
        except Exception as e:
            out_q.put({"type": "final", "result": {"success": False, "error": str(e)}})
        finally:
            out_q.put(None)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while True:
        item = out_q.get()
        if item is None:
            break
        yield item
    thread.join(timeout=720.0)


def maybe_apply_scheduled_theme() -> Dict[str, Any]:
    """If auto-apply is on, apply the theme for the current period when it changes, after failures, or on a re-assert timer."""
    import os
    from datetime import datetime

    if not (os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key")):
        return {"skipped": True, "reason": "no_govee_key"}

    schedule = load_schedule()
    if not schedule.get("autoApplyEnabled", False):
        return {"skipped": True, "reason": "auto_apply_disabled"}

    now = datetime.now()
    period = current_period_for_schedule(schedule, now.hour, now.minute)
    if not period:
        return {"skipped": True, "reason": "no_period_match"}

    theme = theme_for_period(schedule, period)
    state = _load_auto_state()
    same_slot = state.get("last_period") == period and state.get("last_theme") == theme
    if same_slot:
        # Last run may have failed without clearing last_period/theme; retry with backoff
        # so Device Not Found / "not belong you" does not re-hold the Govee lock every minute.
        if state.get("last_success") is False:
            err_age = _seconds_since_iso_timestamp(state.get("last_error_at"), now)
            if err_age is not None and err_age < DAEMON_SCHEDULE_FAILURE_BACKOFF_SEC:
                return {
                    "skipped": True,
                    "reason": "failure_backoff",
                    "period": period,
                    "theme": theme,
                    "retryInSeconds": int(DAEMON_SCHEDULE_FAILURE_BACKOFF_SEC - err_age),
                }
        else:
            reassert_mins = _reassert_theme_minutes(schedule)
            if reassert_mins <= 0:
                return {"skipped": True, "reason": "unchanged", "period": period, "theme": theme}
            elapsed = _seconds_since_iso_timestamp(state.get("last_applied_at"), now)
            if elapsed is not None and elapsed < reassert_mins * 60:
                return {"skipped": True, "reason": "unchanged", "period": period, "theme": theme}
            # Stale apply stamp or clock skew — re-apply to fix bulbs changed in the app or partial cloud drift.

    result = apply_theme(theme)
    _record_scheduled_apply_outcome(state, period, theme, now, bool(result.get("success")))
    result["period"] = period
    result["auto"] = True
    return result
