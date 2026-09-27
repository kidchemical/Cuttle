#!/usr/bin/env python3
"""
Settings Manager for Cuttle
Manages application settings including default pipeline configuration
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

class SettingsManager:
    """Manages persistent application settings"""
    
    FACTORY_DEFAULT_PIPELINE = "OOBE_Welcome"
    
    def __init__(self, settings_file: str = "settings.json"):
        """Initialize settings manager
        
        Args:
            settings_file: Path to settings file (relative to project root)
        """
        self.project_root = Path(__file__).parent.parent
        self.settings_file = self.project_root / settings_file
        self.settings = self._load_settings()
    
    def _load_settings(self) -> Dict[str, Any]:
        """Load settings from file, create with defaults if doesn't exist"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Failed to load settings: {e}")
                return self._get_default_settings()
        else:
            # Create default settings file
            default_settings = self._get_default_settings()
            self._save_settings(default_settings)
            return default_settings

    def reload(self) -> None:
        """Re-read settings from disk.

        Needed because other processes (agents editing config files directly)
        may change settings.json after this manager was constructed. Call
        before read-modify-write on keys that are edited outside this process.
        """
        self.settings = self._load_settings()
    
    def _get_default_settings(self) -> Dict[str, Any]:
        """Get default settings structure"""
        return {
            "version": "1.0.0",
            "default_pipeline": "",
            "factory_default_pipeline": self.FACTORY_DEFAULT_PIPELINE,
            "auto_start_default": False,
            "last_opened_pipeline": None,
            "recent_pipelines": [],
            "user_preferences": {
                "theme": "dark",
                "auto_save": True,
                "show_node_ids": False
            },
            "channels": {
                "webchat": {
                    "dmPolicy": "open",
                    "allowFrom": ["*"]
                },
                "discord": {
                    "dmPolicy": "open",
                    "allowFrom": ["*"]
                },
                "telegram": {
                    "dmPolicy": "open",
                    "allowFrom": ["*"]
                },
                "slack": {
                    "dmPolicy": "open",
                    "allowFrom": ["*"]
                }
            },
            "pipeline_routing": {},
            "starred_slash_commands": [],
            "starred_project": None,
            "sandbox": {
                "enabled": False,
                "restrict_for_session_kinds": ["web_anon", "discord_guild", "discord_dm", "telegram_user", "slack_user"],
                "allowed_tools": ["tool-remote-agent", "llm", "llm-prompt"],
                "denied_tools": [],
                "denied_tool_prefixes": [],
                "allowed_pipelines": ["*"]
            },
            "pipeline_limits": {
                "max_tool_nodes_per_execution": 64
            },
            "discovery": {
                "mdns_enabled": False,
                "lan_access_enabled": False
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
                "token": "",
                "auto_mesh": False,
                "execute_shell_unsafe_enabled": False,
                "execute_shell_enabled": False,
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
            "mcp_downloader": {
                "enabled_packs": ["read_files", "write_files", "delete_files", "system_shell", "python", "playwright"]
            },
            "mcp_servers": {
                "enabled_servers": []
            },
            "inference_preferences": {
                "coding_agent_chain": [
                    "claude",
                    "cursor",
                    "cursor_then_claude",
                    "claw",
                ],
                "llm_fallback_chain": ["local", "anthropic", "openai"],
                "local_model": "",
                "anthropic_model": "claude-sonnet-4-6",
                "openai_model": "gpt-4o-mini",
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
        }
    
    def _save_settings(self, settings: Dict[str, Any]) -> bool:
        """Save settings to file
        
        Args:
            settings: Settings dictionary to save
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2)
            return True
        except Exception as e:
            print(f"Error: Failed to save settings: {e}")
            return False
    
    def get_default_pipeline(self) -> str:
        """Get the default pipeline name (empty string if none)."""
        name = self.settings.get('default_pipeline')
        if name is None:
            return ""
        return str(name).strip()
    
    def set_default_pipeline(self, pipeline_name: str) -> bool:
        """Set the default pipeline, or clear it with an empty name."""
        name = (pipeline_name or "").strip()
        if not name:
            self.settings['default_pipeline'] = ""
            return self._save_settings(self.settings)

        pipelines_dir = self.project_root / 'pipelines'
        pipeline_path = pipelines_dir / f'{name}.json'
        
        if not pipeline_path.exists():
            print(f"Error: Pipeline '{name}' not found")
            return False
        
        self.settings['default_pipeline'] = name
        return self._save_settings(self.settings)
    
    def get_factory_default_pipeline(self) -> str:
        """Get the factory default pipeline (cannot be changed)
        
        Returns:
            Name of the factory default pipeline
        """
        return self.FACTORY_DEFAULT_PIPELINE
    
    def reset_to_factory_default(self) -> bool:
        """Reset default pipeline to factory default
        
        Returns:
            True if successful
        """
        factory_path = self.get_pipeline_path(self.FACTORY_DEFAULT_PIPELINE)
        self.settings['default_pipeline'] = self.FACTORY_DEFAULT_PIPELINE if factory_path else ""
        return self._save_settings(self.settings)
    
    def should_auto_start(self) -> bool:
        """Check if default pipeline should auto-start."""
        return bool(self.settings.get('auto_start_default', False))
    
    def set_auto_start(self, enabled: bool) -> bool:
        """Enable or disable auto-start of default pipeline
        
        Args:
            enabled: True to enable auto-start, False to disable
            
        Returns:
            True if successful
        """
        self.settings['auto_start_default'] = enabled
        return self._save_settings(self.settings)
    
    def get_pipeline_path(self, pipeline_name: str) -> Optional[Path]:
        """Get full path to a pipeline file
        
        Args:
            pipeline_name: Name of pipeline (without .json extension)
            
        Returns:
            Path to pipeline file, or None if not found
        """
        pipelines_dir = self.project_root / 'pipelines'
        pipeline_path = pipelines_dir / f'{pipeline_name}.json'
        
        if pipeline_path.exists():
            return pipeline_path
        return None
    
    def is_factory_default(self, pipeline_name: str) -> bool:
        """Check if a pipeline is the factory default
        
        Args:
            pipeline_name: Name of pipeline to check
            
        Returns:
            True if this is the factory default pipeline
        """
        return pipeline_name == self.FACTORY_DEFAULT_PIPELINE
    
    def list_available_pipelines(self) -> list:
        """List all available pipelines
        
        Returns:
            List of pipeline names (without .json extension)
        """
        pipelines_dir = self.project_root / 'pipelines'
        if not pipelines_dir.exists():
            return []
        
        pipelines = []
        for file in pipelines_dir.glob('*.json'):
            pipelines.append(file.stem)
        
        return sorted(pipelines)
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a specific setting value
        
        Args:
            key: Setting key to retrieve
            default: Default value if key doesn't exist
            
        Returns:
            Setting value or default
        """
        return self.settings.get(key, default)
    
    def set_setting(self, key: str, value: Any) -> bool:
        """Set a specific setting value
        
        Args:
            key: Setting key to set
            value: Value to set
            
        Returns:
            True if successful
        """
        self.settings[key] = value
        return self._save_settings(self.settings)
    
    def get_all_settings(self) -> Dict[str, Any]:
        """Get all settings

        Returns:
            Dictionary of all settings
        """
        return self.settings.copy()

    def get_channel_config(self, channel: str) -> Dict[str, Any]:
        """Get channel security config (dmPolicy, allowFrom) for webchat or discord."""
        channels = self.settings.get("channels") or {}
        defaults = {"dmPolicy": "open", "allowFrom": ["*"]}
        return {**defaults, **channels.get(channel, {})}

    def set_channel_config(self, channel: str, dm_policy: Optional[str] = None, allow_from: Optional[list] = None) -> bool:
        """Update channel config. None leaves existing value unchanged."""
        if "channels" not in self.settings:
            self.settings["channels"] = {}
        if channel not in self.settings["channels"]:
            self.settings["channels"][channel] = {"dmPolicy": "open", "allowFrom": ["*"]}
        if dm_policy is not None:
            self.settings["channels"][channel]["dmPolicy"] = dm_policy
        if allow_from is not None:
            self.settings["channels"][channel]["allowFrom"] = allow_from
        return self._save_settings(self.settings)

    def get_pipeline_routing(self) -> Dict[str, str]:
        """Get pipeline routing map: routing_key -> pipeline_name. Empty means use first running pipeline with trigger."""
        return dict(self.settings.get("pipeline_routing") or {})

    def set_pipeline_route(self, routing_key: str, pipeline_name: Optional[str]) -> bool:
        """Set or clear pipeline for a routing key (e.g. discord_guild_123, web_user_456, main). None clears."""
        if "pipeline_routing" not in self.settings:
            self.settings["pipeline_routing"] = {}
        if pipeline_name is None:
            self.settings["pipeline_routing"].pop(routing_key, None)
        else:
            self.settings["pipeline_routing"][routing_key] = pipeline_name
        return self._save_settings(self.settings)

    def get_sandbox_config(self) -> Dict[str, Any]:
        """Get sandbox config (enabled, restrict_for_session_kinds, allowed/denied tools, allowed_pipelines)."""
        default = {
            "enabled": False,
            "restrict_for_session_kinds": ["web_anon", "discord_guild", "discord_dm", "telegram_user", "slack_user"],
            "allowed_tools": ["tool-remote-agent", "llm", "llm-prompt"],
            "denied_tools": [],
            "denied_tool_prefixes": [],
            "allowed_pipelines": ["*"],
        }
        return {**default, **(self.settings.get("sandbox") or {})}

    def set_sandbox_config(
        self,
        enabled: Optional[bool] = None,
        allowed_tools: Optional[list] = None,
        allowed_pipelines: Optional[list] = None,
        denied_tools: Optional[list] = None,
        denied_tool_prefixes: Optional[list] = None,
        restrict_for_session_kinds: Optional[list] = None,
    ) -> bool:
        """Update sandbox config. None leaves existing value unchanged."""
        if "sandbox" not in self.settings:
            self.settings["sandbox"] = self.get_sandbox_config()
        if enabled is not None:
            self.settings["sandbox"]["enabled"] = enabled
        if allowed_tools is not None:
            self.settings["sandbox"]["allowed_tools"] = allowed_tools
        if allowed_pipelines is not None:
            self.settings["sandbox"]["allowed_pipelines"] = allowed_pipelines
        if denied_tools is not None:
            self.settings["sandbox"]["denied_tools"] = denied_tools
        if denied_tool_prefixes is not None:
            self.settings["sandbox"]["denied_tool_prefixes"] = denied_tool_prefixes
        if restrict_for_session_kinds is not None:
            self.settings["sandbox"]["restrict_for_session_kinds"] = restrict_for_session_kinds
        return self._save_settings(self.settings)

    def get_pipeline_limits(self) -> Dict[str, Any]:
        """Limits for pipeline execution (e.g. max tool nodes per run)."""
        default = {"max_tool_nodes_per_execution": 64}
        return {**default, **(self.settings.get("pipeline_limits") or {})}

    def set_pipeline_limits(self, max_tool_nodes_per_execution: Optional[int] = None) -> bool:
        """Update pipeline_limits. None leaves existing value unchanged."""
        if "pipeline_limits" not in self.settings:
            self.settings["pipeline_limits"] = self.get_pipeline_limits()
        if max_tool_nodes_per_execution is not None:
            self.settings["pipeline_limits"]["max_tool_nodes_per_execution"] = int(max_tool_nodes_per_execution)
        return self._save_settings(self.settings)

    def get_mcp_downloader_config(self) -> Dict[str, Any]:
        """Get MCP downloader config: enabled_packs list (default all packs on)."""
        default = {"enabled_packs": ["read_files", "write_files", "delete_files", "system_shell", "python", "playwright"]}
        return {**default, **(self.settings.get("mcp_downloader") or {})}

    def set_mcp_downloader_pack_enabled(self, pack_id: str, enabled: bool) -> bool:
        """Enable or disable a single pack. pack_id: read_files, write_files, delete_files, system_shell, python, playwright."""
        if "mcp_downloader" not in self.settings:
            self.settings["mcp_downloader"] = self.get_mcp_downloader_config()
        packs = list(self.settings["mcp_downloader"].get("enabled_packs") or [])
        if enabled and pack_id not in packs:
            packs.append(pack_id)
        elif not enabled and pack_id in packs:
            packs.remove(pack_id)
        self.settings["mcp_downloader"]["enabled_packs"] = packs
        return self._save_settings(self.settings)

    def get_mcp_servers_config(self) -> Dict[str, Any]:
        """MCP server registry: enabled_servers lists server ids (e.g. 'default' for Cuttle tools)."""
        default = {"enabled_servers": []}
        return {**default, **(self.settings.get("mcp_servers") or {})}

    def set_mcp_server_enabled(self, server_id: str, enabled: bool) -> bool:
        if "mcp_servers" not in self.settings:
            self.settings["mcp_servers"] = self.get_mcp_servers_config()
        servers = list(self.settings["mcp_servers"].get("enabled_servers") or [])
        if enabled and server_id not in servers:
            servers.append(server_id)
        elif not enabled and server_id in servers:
            servers.remove(server_id)
        self.settings["mcp_servers"]["enabled_servers"] = servers
        return self._save_settings(self.settings)


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

