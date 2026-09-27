"""Govee Developer API v1 client (matches mcp-govee / developer-api.govee.com)."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import requests

GOVEE_API_BASE = "https://developer-api.govee.com/v1"


def _api_key() -> Optional[str]:
    return os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key")


class GoveeError(Exception):
    """Raised for Govee v1 API failures. ``retry_after_seconds`` is set when the server hints wait time (e.g. 429)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retry_after_seconds: Optional[int] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


def parse_retry_after_seconds(response: requests.Response) -> Optional[int]:
    """Read ``Retry-After`` (seconds or HTTP-date). Cap at 24h. Used by v1 and v2 clients."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return max(0, min(int(raw), 86400))
    try:
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = (dt - now).total_seconds()
        if delta > 0:
            return int(min(delta, 86400))
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _retry_after_from_json_body(text: str) -> Optional[int]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("retry_after", "retryAfter", "wait", "waitSeconds", "retryIn"):
        v = data.get(key)
        if isinstance(v, (int, float)) and v >= 0:
            return int(min(v, 86400))
    msg = data.get("message")
    if isinstance(msg, str) and "try again" in msg.lower():
        m = re.search(r"(\d+)\s*s(?:ec(?:ond)?s?)?", msg, re.I)
        if m:
            return int(min(int(m.group(1)), 86400))
    return None


def _request(
    api_key: str,
    path: str,
    method: str = "GET",
    body: Optional[Dict] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    import time

    headers = {"Govee-API-Key": api_key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    url = f"{GOVEE_API_BASE}{path}"
    max_429_rounds = 10
    for attempt in range(max_429_rounds):
        r = requests.request(method, url, headers=headers, json=body, timeout=30)
        if r.status_code == 429:
            ra = parse_retry_after_seconds(r)
            if ra is None:
                ra = _retry_after_from_json_body(r.text or "")
            if ra is None:
                ra = 60
            if attempt + 1 >= max_429_rounds:
                raise GoveeError(
                    f"Govee API error ({r.status_code}): {r.text}",
                    status_code=r.status_code,
                    retry_after_seconds=int(ra),
                )
            if on_retry:
                on_retry(int(ra), attempt + 1)
            time.sleep(min(int(ra), 150))
            continue
        if not r.ok:
            ra = parse_retry_after_seconds(r) if r.status_code == 429 else None
            if ra is None and r.status_code == 429:
                ra = _retry_after_from_json_body(r.text or "")
            raise GoveeError(
                f"Govee API error ({r.status_code}): {r.text}",
                status_code=r.status_code,
                retry_after_seconds=ra,
            )
        return r.json()
    raise GoveeError("Govee API: rate limit retries exhausted", status_code=429, retry_after_seconds=60)


def govee_list_devices(api_key: Optional[str] = None, on_retry: Optional[Callable[[int, int], None]] = None) -> List[Dict[str, Any]]:
    key = api_key or _api_key()
    if not key:
        raise GoveeError("GOVEE_API_KEY is not set")
    data = _request(key, "/devices", on_retry=on_retry)
    devices = (data.get("data") or {}).get("devices")
    if not isinstance(devices, list):
        raise GoveeError("Unexpected Govee API response: missing devices array")
    out = []
    for d in devices:
        out.append(
            {
                "device_id": d.get("device"),
                "model": d.get("model"),
                "name": d.get("deviceName"),
                "controllable": d.get("controllable"),
                "retrievable": d.get("retrievable"),
                "supported_commands": d.get("supportCmds"),
            }
        )
    return out


def flatten_govee_state_properties(properties: Any) -> Dict[str, Any]:
    """Merge Govee /devices/state ``properties`` list into one dict (name/value or flat key pairs)."""
    out: Dict[str, Any] = {}
    if not isinstance(properties, list):
        return out
    for item in properties:
        if not isinstance(item, dict):
            continue
        if "name" in item and "value" in item:
            out[str(item["name"])] = item["value"]
            continue
        for k, v in item.items():
            out[str(k)] = v
    return out


def govee_get_device_state(
    device_id: str,
    model: str,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    key = api_key or _api_key()
    if not key:
        raise GoveeError("GOVEE_API_KEY is not set")
    from urllib.parse import urlencode

    q = urlencode({"device": device_id, "model": model})
    data = _request(key, f"/devices/state?{q}", on_retry=on_retry)
    inner = data.get("data")
    if not isinstance(inner, dict):
        raise GoveeError("Unexpected Govee API response: missing state data")
    return inner


def govee_control(
    device_id: str,
    model: str,
    cmd: Dict[str, Any],
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    key = api_key or _api_key()
    if not key:
        raise GoveeError("GOVEE_API_KEY is not set")
    body = {"device": device_id, "model": model, "cmd": cmd}
    return _request(key, "/devices/control", method="PUT", body=body, on_retry=on_retry)


def govee_set_power(
    device_id: str,
    model: str,
    state: str,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> str:
    if state not in ("on", "off"):
        raise GoveeError("state must be 'on' or 'off'")
    govee_control(device_id, model, {"name": "turn", "value": state}, api_key=api_key, on_retry=on_retry)
    return f"Device turned {state}."


def govee_set_brightness(
    device_id: str,
    model: str,
    brightness: int,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> str:
    if not isinstance(brightness, int) or brightness < 0 or brightness > 100:
        raise GoveeError("brightness must be an integer 0-100")
    govee_control(
        device_id, model, {"name": "brightness", "value": brightness}, api_key=api_key, on_retry=on_retry
    )
    return f"Brightness set to {brightness}%."


def govee_set_color(
    device_id: str,
    model: str,
    r: int,
    g: int,
    b: int,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> str:
    for name, v in (("r", r), ("g", g), ("b", b)):
        if not isinstance(v, int) or v < 0 or v > 255:
            raise GoveeError(f"{name} must be an integer 0-255")
    govee_control(
        device_id,
        model,
        {"name": "color", "value": {"r": r, "g": g, "b": b}},
        api_key=api_key,
        on_retry=on_retry,
    )
    return f"Color set to RGB({r}, {g}, {b})."


def govee_set_color_temperature(
    device_id: str,
    model: str,
    temperature: int,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> str:
    if not isinstance(temperature, int) or temperature < 2000 or temperature > 9000:
        raise GoveeError("temperature must be an integer 2000-9000 (Kelvin)")
    govee_control(
        device_id, model, {"name": "colorTem", "value": temperature}, api_key=api_key, on_retry=on_retry
    )
    return f"Color temperature set to {temperature}K."
