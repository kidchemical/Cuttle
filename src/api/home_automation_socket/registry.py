"""Register home-automation providers (Govee live, Nest stub, …)."""

from __future__ import annotations

from typing import Dict, List, Optional

from api.home_automation_socket.providers.govee import GoveeProvider
from api.home_automation_socket.providers.nest import NestProvider
from api.home_automation_socket.types import DeviceInfo, HomeAutomationProvider, ProviderInfo

_PROVIDERS: Dict[str, HomeAutomationProvider] = {
    GoveeProvider.id: GoveeProvider(),
    NestProvider.id: NestProvider(),
}


def list_provider_ids() -> List[str]:
    return list(_PROVIDERS.keys())


def get_provider(provider_id: str) -> Optional[HomeAutomationProvider]:
    return _PROVIDERS.get((provider_id or "").strip().lower())


def list_providers(*, include_devices: bool = False) -> List[ProviderInfo]:
    rows: List[ProviderInfo] = []
    for pid, provider in _PROVIDERS.items():
        devices: List[DeviceInfo] = []
        count = 0
        if provider.available():
            try:
                devices = provider.list_devices()
                count = len(devices)
            except Exception:
                devices = []
                count = 0
        rows.append(
            ProviderInfo(
                id=pid,
                label=provider.label,
                available=bool(provider.available()),
                status=provider.status(),
                install_hint=provider.install_hint(),
                capabilities=list(provider.capabilities()),
                device_count=count if include_devices or provider.available() else 0,
            )
        )
    return rows


def list_all_devices() -> List[DeviceInfo]:
    out: List[DeviceInfo] = []
    for provider in _PROVIDERS.values():
        if not provider.available():
            continue
        try:
            out.extend(provider.list_devices())
        except Exception:
            continue
    return out
