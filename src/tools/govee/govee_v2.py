"""Govee Open API v2 (router) — dynamic scenes only; used when v1 cannot animate."""

from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, List, Optional

import requests

from tools.govee.govee_api import _retry_after_from_json_body, parse_retry_after_seconds

V2_BASE = "https://openapi.api.govee.com/router/api/v1"
CAP_SCENE = "devices.capabilities.dynamic_scene"


def _api_key() -> Optional[str]:
    return os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key")


class GoveeV2Error(Exception):
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


def _v2_post(
    api_key: str,
    path: str,
    payload: Dict[str, Any],
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    headers = {"Govee-API-Key": api_key, "Content-Type": "application/json"}
    url = f"{V2_BASE}{path}"
    max_429_rounds = 10
    for attempt in range(max_429_rounds):
        body = {"requestId": str(uuid.uuid4()), "payload": payload}
        r = requests.post(url, headers=headers, json=body, timeout=45)
        if r.status_code == 429:
            ra = parse_retry_after_seconds(r)
            if ra is None:
                ra = _retry_after_from_json_body(r.text or "")
            if ra is None:
                ra = 60
            if attempt + 1 >= max_429_rounds:
                raise GoveeV2Error(
                    f"Govee v2 API error ({r.status_code}): {r.text}",
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
            raise GoveeV2Error(
                f"Govee v2 API error ({r.status_code}): {r.text}",
                status_code=r.status_code,
                retry_after_seconds=ra,
            )
        data = r.json()
        if not isinstance(data, dict):
            raise GoveeV2Error("Unexpected Govee v2 response")
        code = data.get("code")
        if code is not None and int(code) not in (0, 200):
            raise GoveeV2Error(data.get("message") or str(data))
        return data
    raise GoveeV2Error("Govee v2 API: rate limit retries exhausted", status_code=429, retry_after_seconds=60)


def _v2_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    p = data.get("payload")
    if isinstance(p, dict):
        return p
    nested = data.get("data")
    if isinstance(nested, dict):
        p2 = nested.get("payload")
        if isinstance(p2, dict):
            return p2
    raise GoveeV2Error("Unexpected Govee v2 response: missing payload")


def govee_v2_list_light_scenes(
    device_id: str,
    model: str,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> List[Dict[str, Any]]:
    key = api_key or _api_key()
    if not key:
        raise GoveeV2Error("GOVEE_API_KEY is not set")
    data = _v2_post(key, "/device/scenes", {"sku": model, "device": device_id}, on_retry=on_retry)
    payload = _v2_payload(data)
    caps = payload.get("capabilities") or []
    for cap in caps:
        if not isinstance(cap, dict):
            continue
        if cap.get("type") == CAP_SCENE and cap.get("instance") == "lightScene":
            params = cap.get("parameters") or {}
            opts = params.get("options") or []
            if isinstance(opts, list):
                return opts
    return []


def govee_v2_find_light_scene_value(
    device_id: str,
    model: str,
    scene_name: str,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> Any:
    key = api_key or _api_key()
    if not key:
        raise GoveeV2Error("GOVEE_API_KEY is not set")
    options = govee_v2_list_light_scenes(device_id, model, api_key=key, on_retry=on_retry)
    target = scene_name.strip().lower()
    for o in options:
        if not isinstance(o, dict):
            continue
        name = (o.get("name") or "").strip().lower()
        if name == target:
            value = o.get("value")
            if value is None:
                raise GoveeV2Error(f"Scene {scene_name!r} has no value")
            return value
    names = [o.get("name") for o in options if isinstance(o, dict)]
    raise GoveeV2Error(f"Scene {scene_name!r} not found. Available: {names[:15]}...")


def govee_v2_apply_light_scene_value(
    device_id: str,
    model: str,
    scene_value: Any,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> None:
    key = api_key or _api_key()
    if not key:
        raise GoveeV2Error("GOVEE_API_KEY is not set")
    capability = {"type": CAP_SCENE, "instance": "lightScene", "value": scene_value}
    _v2_post(
        key,
        "/device/control",
        {"sku": model, "device": device_id, "capability": capability},
        on_retry=on_retry,
    )


def govee_v2_activate_light_scene(
    device_id: str,
    model: str,
    scene_name: str,
    api_key: Optional[str] = None,
    on_retry: Optional[Callable[[int, int], None]] = None,
) -> None:
    val = govee_v2_find_light_scene_value(device_id, model, scene_name, api_key=api_key, on_retry=on_retry)
    govee_v2_apply_light_scene_value(device_id, model, val, api_key=api_key, on_retry=on_retry)
