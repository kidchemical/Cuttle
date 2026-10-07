"""
Pairing Manager for Cuttle
Channel-level security: pairing and allowFrom for authenticated Web Chat.
Unknown senders receive a pairing code; approve via API to add to allowlist.
"""

import json
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from core.runtime_paths import cuttle_home

PAIRING_STORE_FILE = cuttle_home() / "pairing_store.json"

# Code validity seconds
CODE_TTL_SECONDS = 600  # 10 minutes


class PairingManager:
    """Manages pairing codes and approved identities per channel."""

    def __init__(self, store_path: Optional[Path] = None):
        self.store_path = store_path or PAIRING_STORE_FILE
        self._ensure_data_dir()
        self._store = self._load_store()

    def _ensure_data_dir(self) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_store(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return {"approved": {}, "pending": {}}
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"approved": {}, "pending": {}}

    def _save_store(self) -> bool:
        try:
            with open(self.store_path, "w", encoding="utf-8") as f:
                json.dump(self._store, f, indent=2)
            return True
        except Exception:
            return False

    def _identity_key(self, channel: str, identity: str) -> str:
        return f"{channel}:{identity}"

    def is_allowed(
        self,
        channel: str,
        identity: str,
        dm_policy: str = "open",
        allow_from: Optional[List[str]] = None,
    ) -> bool:
        """
        Check if identity is allowed to use the channel.
        allow_from: list of allowed IDs, or ["*"] for everyone when dm_policy is "open".
        """
        if allow_from is None:
            allow_from = ["*"]
        if "*" in allow_from and dm_policy == "open":
            return True
        key = self._identity_key(channel, identity)
        if key in self._store.get("approved", {}):
            return True
        if identity in allow_from or str(identity) in [str(x) for x in allow_from]:
            return True
        return False

    def needs_pairing(
        self,
        channel: str,
        identity: str,
        dm_policy: str = "open",
        allow_from: Optional[List[str]] = None,
    ) -> bool:
        """True if identity must complete pairing (dm_policy is pairing and not allowed)."""
        if dm_policy != "pairing":
            return False
        return not self.is_allowed(channel, identity, dm_policy, allow_from)

    def get_pending_code(self, channel: str, identity: str) -> Optional[str]:
        """Return existing pending code for identity if still valid."""
        key = self._identity_key(channel, identity)
        pending = self._store.get("pending", {}).get(key)
        if not pending:
            return None
        if time.time() - pending.get("created_at", 0) > CODE_TTL_SECONDS:
            self._remove_pending(key)
            return None
        return pending.get("code")

    def generate_pairing_code(self, channel: str, identity: str, meta: Optional[Dict] = None) -> str:
        """Generate a new 6-character pairing code for identity. Replaces any existing pending."""
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        key = self._identity_key(channel, identity)
        if "pending" not in self._store:
            self._store["pending"] = {}
        self._store["pending"][key] = {
            "code": code,
            "identity": identity,
            "channel": channel,
            "created_at": time.time(),
            "meta": meta or {},
        }
        self._save_store()
        return code

    def _remove_pending(self, key: str) -> None:
        if "pending" in self._store and key in self._store["pending"]:
            del self._store["pending"][key]
            self._save_store()

    def approve(self, code: str) -> Tuple[bool, Optional[str]]:
        """
        Approve a pairing code. Adds identity to approved list.
        Returns (success, identity_key or error message).
        """
        code = (code or "").strip().upper()
        for key, pending in list(self._store.get("pending", {}).items()):
            if pending.get("code") == code:
                if time.time() - pending.get("created_at", 0) > CODE_TTL_SECONDS:
                    del self._store["pending"][key]
                    self._save_store()
                    return False, "Pairing code expired."
                if "approved" not in self._store:
                    self._store["approved"] = {}
                self._store["approved"][key] = {
                    "approved_at": time.time(),
                    "meta": pending.get("meta", {}),
                }
                del self._store["pending"][key]
                self._save_store()
                return True, key
        return False, "Invalid or expired pairing code."

    def check_access(
        self,
        channel: str,
        identity: str,
        dm_policy: str = "open",
        allow_from: Optional[List[str]] = None,
        meta: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Full check: allowed, needs_pairing, or pending code.
        Returns dict: allowed (bool), pairing_required (bool), pairing_code (str|None), message (str).
        """
        if allow_from is None:
            allow_from = ["*"]
        if self.is_allowed(channel, identity, dm_policy, allow_from):
            return {
                "allowed": True,
                "pairing_required": False,
                "pairing_code": None,
                "message": None,
            }
        if dm_policy != "pairing":
            return {
                "allowed": False,
                "pairing_required": False,
                "pairing_code": None,
                "message": "Access denied. Not in allowlist.",
            }
        existing = self.get_pending_code(channel, identity)
        if existing:
            return {
                "allowed": False,
                "pairing_required": True,
                "pairing_code": existing,
                "message": f"Pairing required. Your code is: {existing}. An owner must approve this code in Settings or via API.",
            }
        code = self.generate_pairing_code(channel, identity, meta)
        return {
            "allowed": False,
            "pairing_required": True,
            "pairing_code": code,
            "message": f"Pairing required. Your code is: {code}. An admin must approve this code in Settings or via API.",
        }

    def list_pending(self) -> List[Dict[str, Any]]:
        """List all pending pairing requests (for admin UI)."""
        out = []
        now = time.time()
        for key, p in self._store.get("pending", {}).items():
            if now - p.get("created_at", 0) > CODE_TTL_SECONDS:
                continue
            out.append({
                "key": key,
                "code": p.get("code"),
                "channel": p.get("channel"),
                "identity": p.get("identity"),
                "created_at": p.get("created_at"),
                "meta": p.get("meta", {}),
            })
        return out

    def list_approved(self, channel: Optional[str] = None) -> List[str]:
        """List approved identity keys, optionally filtered by channel."""
        keys = list(self._store.get("approved", {}).keys())
        if channel:
            keys = [k for k in keys if k.startswith(channel + ":")]
        return keys


_pairing_manager: Optional[PairingManager] = None


def get_pairing_manager() -> PairingManager:
    global _pairing_manager
    if _pairing_manager is None:
        _pairing_manager = PairingManager()
    return _pairing_manager
