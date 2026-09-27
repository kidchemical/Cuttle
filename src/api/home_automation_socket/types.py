"""Home automation socket — provider protocol and catalog types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class DeviceInfo:
    device_id: str
    name: str
    model: str = ""
    zone: str = ""
    kind: str = "light"  # light | camera | thermostat | other
    provider_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    available: bool
    status: str  # ready | missing_ creds | stub
    install_hint: str = ""
    capabilities: List[str] = field(default_factory=list)
    device_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@runtime_checkable
class HomeAutomationProvider(Protocol):
    """Vendor plug for the home-automation socket."""

    id: str
    label: str

    def available(self) -> bool:
        """True when this machine can talk to the vendor (creds + optional deps)."""
        ...

    def status(self) -> str:
        """ready | missing_creds | stub"""
        ...

    def install_hint(self) -> str:
        ...

    def capabilities(self) -> List[str]:
        """e.g. lights, themes, schedule, cameras"""
        ...

    def list_devices(self) -> List[DeviceInfo]:
        ...
