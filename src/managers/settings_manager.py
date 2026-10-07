#!/usr/bin/env python3
"""
Settings Manager for Cuttle
Manages server preferences, machine configuration, and saved UI state
"""

import copy
from pathlib import Path
from typing import Optional, Dict, Any

class SettingsManager:
    """Manages persistent application settings"""
    
    def __init__(self, settings_file: Optional[str] = None):
        """Initialize settings manager

        Args:
            settings_file: Explicit settings path (tests); default is the
                per-user home's ``config/settings.json``.
        """
        from managers.settings_storage import storage_for
        self.storage = storage_for(Path(settings_file) if settings_file else None)
        self.settings_file = self.storage.server
        self.settings = self._load_settings()
    
    def _load_settings(self) -> Dict[str, Any]:
        """Reads create nothing; malformed files fail instead of being overwritten."""
        return {**self._get_default_settings(), **self.storage.read()}

    def reload(self) -> None:
        self.settings = self._load_settings()

    def _get_default_settings(self) -> Dict[str, Any]:
        """Get default settings structure"""
        return {
            "channels": {
                "webchat": {
                    "dmPolicy": "open",
                    "allowFrom": ["*"]
                }
            },
            "starred_slash_commands": [],
            "starred_project": None,
            "discovery": {
                "mdns_enabled": False,
                "lan_access_enabled": False
            },
            # Self-registration closes once the owner account exists.
            "auth": {
                "allow_registration": False
            },
            "device_workers": {
                "enabled": True,
                "coordinator_url": "",
                "worker_id": "",
                "poll_seconds": 5,
                "lease_seconds": 600,
                "stale_seconds": 45,
                "interactive_priority": "low",
                "local_worker": True,
                "allowed_path_prefixes": [],
                "auto_mesh": False,
                "execute_shell_unsafe_enabled": False,
                "execute_shell_ssh_enabled": False,
                "ssh_host": "",
                "ssh_user": "",
                "ssh_port": 22,
                "ssh_identity": "",
                "execute_shell_prefixes": [],
                "shell_recipes": {},
                "ssh_approval_required": True,
                "ssh_approval_timeout_seconds": 300,
                "queue_ttl_seconds": {},
                "max_attempts": {},
                "default_queue_ttl_seconds": 21600,
                "default_max_attempts": 2,
                "no_requeue_types": [],
            },
            "chat_tts": {
                "enabled": True,
                "tts_model": "gpt-4o-mini-tts",
                "voice": "coral",
                "summarize": True,
                "summarize_model": "gpt-4o-mini",
                "target_spoken_chars": 420,
                "skip_summarize_under_chars": 380,
                "max_input_chars": 24000,
            },
            "agent_router": {
                "provider": {
                    "mode": "api",
                    "api_provider": "openai",
                    "api_model": "gpt-4o-mini",
                    "local_endpoint": "",
                    "local_model": "",
                    "agent_id": "cursor",
                    "agent_model": "auto",
                },
                "default_target": {"agent": "cursor", "model": "auto"},
                "escalation_target": {"agent": "cursor", "model": "grok-4.6"},
                "fallbacks": {
                    "ordered": [{"agent": "codex", "model": ""}]
                },
            },
            # Experimental feature flags: {flag_id: bool}. Owned by
            # api.experimental (registry + resolver live there); this key is
            # just the storage. Absent ids fall back to the registry default.
            "experimental_flags": {},
        }
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a specific setting value
        
        Args:
            key: Setting key to retrieve
            default: Default value if key doesn't exist
            
        Returns:
            Setting value or default
        """
        self.reload()
        return copy.deepcopy(self.settings.get(key, default))
    
    def set_setting(self, key: str, value: Any) -> bool:
        """Set a specific setting value
        
        Args:
            key: Setting key to set
            value: Value to set
            
        Returns:
            True if successful
        """
        return self.update_setting(key, lambda current: value)

    def update_setting(self, key: str, transform) -> bool:
        """Apply a read-modify-write under the shared process/thread lock."""
        try:
            self.storage.update(key, transform)
            self.reload()
            return True
        except (OSError, ValueError, TypeError) as exc:
            print(f"Error: Failed to save setting {key}: {exc}")
            return False
    
    def get_all_settings(self) -> Dict[str, Any]:
        """Get all settings

        Returns:
            Dictionary of all settings
        """
        self.reload()
        return copy.deepcopy(self.settings)

    def get_channel_config(self, channel: str) -> Dict[str, Any]:
        """Get live Web Chat security configuration."""
        channels = self.get_setting("channels") or {}
        defaults = {"dmPolicy": "open", "allowFrom": ["*"]}
        return {**defaults, **channels.get(channel, {})}

    def set_channel_config(self, channel: str, dm_policy: Optional[str] = None, allow_from: Optional[list] = None) -> bool:
        """Update channel config. None leaves existing value unchanged."""
        if channel != "webchat":
            return False

        def apply(current):
            channels = dict(current or {})
            config = {"dmPolicy": "open", "allowFrom": ["*"], **channels.get(channel, {})}
            if dm_policy is not None:
                config["dmPolicy"] = dm_policy
            if allow_from is not None:
                config["allowFrom"] = allow_from
            channels[channel] = config
            return channels

        return self.update_setting("channels", apply)

# Global instance
_settings_manager = None

def get_settings_manager() -> SettingsManager:
    """Get the global settings manager instance
    
    Returns:
        SettingsManager instance
    """
    global _settings_manager
    if _settings_manager is None:
        _settings_manager = SettingsManager()
    return _settings_manager

