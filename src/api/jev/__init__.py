"""
Jev — Cuttle's System One judgment socket.

Not a tentacle. Callers assemble state; Jev returns Choice / Score / Noul;
policy stays in Python. Debug CLI: ``python -m api.jev``.
"""

from api.jev.client import (
    FakeJevClient,
    JevClient,
    JevError,
    get_client,
    jev_available,
    set_client_override,
)
from api.jev.config import is_jev_model_id, load_jev_config, resolve_jev_credentials

__all__ = [
    "FakeJevClient",
    "JevClient",
    "JevError",
    "get_client",
    "is_jev_model_id",
    "jev_available",
    "load_jev_config",
    "resolve_jev_credentials",
    "set_client_override",
]
