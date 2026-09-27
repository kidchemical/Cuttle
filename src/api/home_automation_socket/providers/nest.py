"""Nest / Google Home stub — implement when Device Access / SDM credentials exist."""

from __future__ import annotations

from typing import List

from api.home_automation_socket.types import DeviceInfo


class NestProvider:
    """Placeholder plug for Nest cams / thermostats / displays.

    Not wired yet. When you are ready: Google Device Access project + OAuth,
    then implement list_devices / stream hooks without touching Flask routing.
    """

    id = "nest"
    label = "Google Nest"

    def available(self) -> bool:
        # Flip to True + real SDM calls once this provider is implemented.
        return False

    def status(self) -> str:
        return "stub"

    def install_hint(self) -> str:
        return (
            "Nest is a planned provider (cameras / thermostats via Google Smart Device Management). "
            "Not implemented yet — when ready, set NEST_CLIENT_ID, NEST_CLIENT_SECRET, and "
            "NEST_PROJECT_ID in src/.env and flesh out providers/nest.py."
        )

    def capabilities(self) -> List[str]:
        return ["cameras", "thermostats"]

    def list_devices(self) -> List[DeviceInfo]:
        return []
