"""
Configuration system for the Discord bot with different operation modes.
"""

import os
from typing import Dict, Any
from pathlib import Path

class BotConfig:
    """Bot configuration with different operation modes"""
    
    def __init__(self):
        self.config_file = Path("bot_config.json")
        self.default_config = {
            "mode": "default",  # default, multi_stage, llm_only, regex_only
            "thinking_response": True,  # Show "Thinking..." when using LLM
            "auto_restart": False,  # Auto-restart on file changes
            "debug_mode": False,  # Enable debug logging
            "llm_threshold": 0.7,  # Confidence threshold for LLM usage in multi_stage mode
            "cursor_agent_method": "native",  # native, wsl, ui_automation
            # New agent settings
            "agent_stage_mode": "multi-lite",  # single, multi, multi-lite
            "preferred_llm_model": "gpt-4o-mini",  # Default LLM model
            "preferred_ollama_model": "llama3",  # Default local (Ollama) model; used when pipeline node is set to "default"
            "preferred_tools_llm_model": "gpt-4o-mini",  # Default model used for tool-calling (cloud)
            # Qwen 2.5 follows OpenAI-style tool_calls reliably on Ollama for many setups; pull if missing.
            "preferred_tools_ollama_model": "qwen2.5:latest",  # Default for tool-calling (local/Ollama)
            "agent_name": "Cuttle",  # Agent name for chat and system messages
            "llm_fallback_enabled": True,  # Enable LLM model fallback
            "system_prompt_mode": "default",  # default, custom
            "custom_system_prompt": "",  # Custom system prompt text
        }
        self.config = self.load_config()
    
    def load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default"""
        import json
        
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    # Merge with defaults to handle new config options
                    merged_config = self.default_config.copy()
                    merged_config.update(config)
                    return merged_config
            except Exception as e:
                print(f"⚠️ Error loading config: {e}, using defaults")
                return self.default_config.copy()
        else:
            # Create default config file
            self.save_config(self.default_config)
            return self.default_config.copy()
    
    def save_config(self, config: Dict[str, Any] = None):
        """Save configuration to file"""
        import json
        
        if config is None:
            config = self.config
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"⚠️ Error saving config: {e}")
    
    def get(self, key: str, default=None):
        """Get configuration value"""
        return self.config.get(key, default)
    
    def set(self, key: str, value: Any):
        """Set configuration value and save"""
        self.config[key] = value
        self.save_config()
    
    def get_mode(self) -> str:
        """Get current operation mode"""
        return self.config.get("mode", "default")
    
    def set_mode(self, mode: str):
        """Set operation mode"""
        valid_modes = ["default", "multi_stage", "llm_only", "regex_only"]
        if mode in valid_modes:
            self.set("mode", mode)
            return True
        return False
    
    def should_show_thinking(self) -> bool:
        """Check if thinking response should be shown"""
        return self.config.get("thinking_response", True)
    
    def is_debug_mode(self) -> bool:
        """Check if debug mode is enabled"""
        return self.config.get("debug_mode", False)
    
    def get_cursor_agent_method(self) -> str:
        """Get cursor-agent method (native, wsl, ui_automation)"""
        return self.config.get("cursor_agent_method", "native")
    
    def set_cursor_agent_method(self, method: str) -> bool:
        """Set cursor-agent method"""
        valid_methods = ["native", "wsl", "ui_automation"]
        if method in valid_methods:
            self.set("cursor_agent_method", method)
            return True
        return False
    
    def get_agent_stage_mode(self) -> str:
        """Get agent stage mode (single, multi, multi-lite)"""
        return self.config.get("agent_stage_mode", "multi-lite")
    
    def set_agent_stage_mode(self, mode: str) -> bool:
        """Set agent stage mode"""
        valid_modes = ["single", "multi", "multi-lite"]
        if mode in valid_modes:
            self.set("agent_stage_mode", mode)
            return True
        return False
    
    def get_preferred_llm_model(self) -> str:
        """Get preferred LLM model"""
        return self.config.get("preferred_llm_model", "gpt-4o-mini")
    
    def set_preferred_llm_model(self, model: str) -> bool:
        """Set preferred LLM model"""
        valid_models = [
            "gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo",
            "claude-3-5-sonnet-latest", "claude-3-haiku", "claude-3-opus"
        ]
        if model in valid_models:
            self.set("preferred_llm_model", model)
            return True
        return False

    def get_preferred_ollama_model(self) -> str:
        """Get preferred local (Ollama) model; used when pipeline Local LLM node is set to 'default'."""
        return self.config.get("preferred_ollama_model", "llama3")

    def set_preferred_ollama_model(self, model: str) -> bool:
        """Set preferred local (Ollama) model. Accepts any non-empty string (Ollama model name)."""
        if model and isinstance(model, str) and model.strip():
            self.set("preferred_ollama_model", model.strip())
            return True
        return False

    def get_preferred_tools_llm_model(self) -> str:
        """Get preferred tool-calling LLM model (cloud)."""
        return self.config.get("preferred_tools_llm_model", self.get_preferred_llm_model())

    def set_preferred_tools_llm_model(self, model: str) -> bool:
        """Set preferred tool-calling LLM model (cloud)."""
        valid_models = [
            "gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo",
            "claude-3-5-sonnet-latest", "claude-3-haiku", "claude-3-opus"
        ]
        if model in valid_models:
            self.set("preferred_tools_llm_model", model)
            return True
        return False

    def get_preferred_tools_ollama_model(self) -> str:
        """Get preferred tool-calling local (Ollama) model."""
        return self.config.get("preferred_tools_ollama_model", self.get_preferred_ollama_model())

    def set_preferred_tools_ollama_model(self, model: str) -> bool:
        """Set preferred tool-calling local (Ollama) model. Accepts any non-empty string."""
        if model and isinstance(model, str) and model.strip():
            self.set("preferred_tools_ollama_model", model.strip())
            return True
        return False

    def get_agent_name(self) -> str:
        """Get agent name"""
        return self.config.get("agent_name", "Cuttle")
    
    def set_agent_name(self, name: str) -> bool:
        """Set agent name"""
        if name and len(name.strip()) > 0 and len(name.strip()) <= 50:
            self.set("agent_name", name.strip())
            return True
        return False
    
    def is_llm_fallback_enabled(self) -> bool:
        """Check if LLM fallback is enabled"""
        return self.config.get("llm_fallback_enabled", True)
    
    def set_llm_fallback_enabled(self, enabled: bool) -> bool:
        """Set LLM fallback enabled/disabled"""
        self.set("llm_fallback_enabled", enabled)
        return True
    
    def get_system_prompt_mode(self) -> str:
        """Get system prompt mode (default, custom)"""
        return self.config.get("system_prompt_mode", "default")
    
    def set_system_prompt_mode(self, mode: str) -> bool:
        """Set system prompt mode"""
        valid_modes = ["default", "custom"]
        if mode in valid_modes:
            self.set("system_prompt_mode", mode)
            return True
        return False
    
    def get_custom_system_prompt(self) -> str:
        """Get custom system prompt text"""
        return self.config.get("custom_system_prompt", "")
    
    def set_custom_system_prompt(self, prompt: str) -> bool:
        """Set custom system prompt text"""
        self.set("custom_system_prompt", prompt)
        return True
    
    def get_effective_system_prompt(self) -> str:
        """Get the effective system prompt based on current mode"""
        if self.get_system_prompt_mode() == "custom":
            custom_prompt = self.get_custom_system_prompt()
            if custom_prompt.strip():
                return custom_prompt
        # Return default system prompt
        return self.get_default_system_prompt()
    
    def get_default_system_prompt(self) -> str:
        """Get the default system prompt"""
        return """You are Cuttle, an AI-powered development assistant. You help users with coding tasks, automation, debugging, and development workflows. You are knowledgeable about various programming languages, frameworks, and development tools. You provide clear, helpful, and accurate responses while being professional and friendly."""

# Global config instance
config = BotConfig()

def get_config() -> BotConfig:
    """Get global config instance"""
    return config

def set_mode(mode: str) -> bool:
    """Set bot operation mode"""
    return config.set_mode(mode)

def get_mode() -> str:
    """Get current bot operation mode"""
    return config.get_mode()
