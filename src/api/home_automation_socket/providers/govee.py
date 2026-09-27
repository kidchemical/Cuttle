"""Govee provider — wraps existing managers.home_automation."""

from __future__ import annotations

import os
from typing import List

from api.home_automation_socket.types import DeviceInfo


class GoveeProvider:
    id = "govee"
    label = "Govee Lights"

    def available(self) -> bool:
        return bool(os.environ.get("GOVEE_API_KEY") or os.environ.get("Govee_API_Key"))

    def status(self) -> str:
        return "ready" if self.available() else "missing_creds"

    def install_hint(self) -> str:
        if self.available():
            return ""
        return (
            "Set GOVEE_API_KEY in src/.env (Govee Home → Settings → About → Apply for API key). "
            "Optional device list: copy src/data/home_automation_devices.example.json "
            "→ home_automation_devices.json."
        )

    def capabilities(self) -> List[str]:
        return ["lights", "themes", "schedule"]

    def list_devices(self) -> List[DeviceInfo]:
        from managers.home_automation import load_govee_devices, refresh_govee_devices

        refresh_govee_devices()
        out: List[DeviceInfo] = []
        for row in load_govee_devices():
            out.append(
                DeviceInfo(
                    device_id=str(row.get("device_id") or ""),
                    name=str(row.get("name") or row.get("device_id") or ""),
                    model=str(row.get("model") or ""),
                    zone=str(row.get("zone") or ""),
                    kind="light",
                    provider_id=self.id,
                )
            )
        return out
