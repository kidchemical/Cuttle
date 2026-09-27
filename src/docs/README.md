# Cuttle Documentation

Welcome to the Cuttle project documentation! Cuttle is a powerful PC automation assistant with Discord integration, AI agent capabilities, and a visual node-based workflow editor.

## 📚 Documentation Structure

### 🚀 [Setup Guides](setup/)
Get started with Cuttle - installation, configuration, and deployment guides.

- **[Setup Instructions](setup/SETUP_INSTRUCTIONS.md)** - Complete installation and configuration guide
- **[WSL & Cursor Agent Setup](setup/WSL_CURSOR_AGENT_SETUP.md)** - Setting up WSL and cursor-agent integration

### 📖 [User Guides](guides/)
Learn how to use Cuttle's features and tools.

- **[Quick Start: Node Editor](guides/QUICK_START_NODE_EDITOR.md)** - 5-minute intro to the visual pipeline builder
- **[Node Editor Guide](guides/NODE_EDITOR_GUIDE.md)** - Complete guide to the node-based workflow editor
- **[MCP Launcher Guide](guides/MCP_LAUNCHER_GUIDE.md)** - Using the Model Context Protocol launcher
- **[Time Series Dashboard](guides/TIME_SERIES_DASHBOARD.md)** - Monitor test failures and trends over time
- **[Pairing and Allowlist](guides/PAIRING_AND_ALLOWLIST.md)** - Channel-level security (DM pairing, allowFrom)
- **[Session and Pipeline Routing](guides/SESSION_AND_PIPELINE_ROUTING.md)** - Session kinds and per-channel pipeline routing
- **[Wizard and Doctor](guides/WIZARD_AND_DOCTOR.md)** - Setup wizard and config/health checks
- **[Telegram and Slack Triggers](guides/TELEGRAM_SLACK_TRIGGERS.md)** - Telegram/Slack trigger types and API
- **[Sandbox](guides/SANDBOX.md)** - Optional sandbox for non-owner sessions
- **[Remote Access](guides/REMOTE_ACCESS.md)** - Tailscale, SSH tunnels, mDNS discovery
- **[Sessions API](guides/SESSIONS_API.md)** - Agent-to-agent (list pipelines/sessions, send to pipeline/session)
- **[Skills Registry](guides/SKILLS_REGISTRY.md)** - Install pipeline templates from the registry
- **[Agent Router](guides/AGENT_ROUTER.md)** — which tentacle runs a clean-session turn
- **[Modularity / harness of harnesses](guides/MODULARITY.md)** — product thesis, sockets, DeepSeek comparison
- **[Cuttle Workers](guides/CUTTLE_WORKERS.md)** — multi-device compute mesh (architecture W0; host-first queue, Blender CLI benchmark)
- **[Versioning](guides/VERSIONING.md)** — desktop/mobile/web version pins and auto-update (hash vs semver)

### 🔧 [Development Documentation](development/)
Technical documentation for contributors and developers.

- **[Directory Structure](development/DIRECTORY_STRUCTURE.md)** - Project organization and file structure
- **[MCP Conversion Summary](development/MCP_CONVERSION_SUMMARY.md)** - Model Context Protocol architecture
- **[Claude Usage Tracking](development/CLAUDE_USAGE_TRACKING.md)** - Claude API integration and usage monitoring
- **[Tools API](development/TOOLS_API.md)** - Complete automation tools reference
- **[RAG System](development/RAG_SYSTEM.md)** - Retrieval-Augmented Generation implementation
- **[Testing Guide](development/TESTING.md)** - Test suite documentation and guidelines
- **[Cache System](development/CACHE_SYSTEM.md)** - Cache management documentation
- **[Output Directory](development/OUTPUT_DIRECTORY.md)** - Generated files and temporary data
- **[Logging Overview](LOGGING.md)** - Canonical reference for all logs, reports, and debug output

## 🎯 Quick Links

### Getting Started
1. **New User?** Start with [Setup Instructions](setup/SETUP_INSTRUCTIONS.md)
2. **Want to build workflows?** Try the [Quick Start: Node Editor](guides/QUICK_START_NODE_EDITOR.md)
3. **Using MCP?** Check the [MCP Launcher Guide](guides/MCP_LAUNCHER_GUIDE.md)

### Key Features
- 🤖 **Discord Bot** - AI-powered Discord assistant with natural language commands
- 🎨 **Node Editor** - Visual workflow builder similar to n8n
- 🔧 **Automation Tools** - Process, window, input, screenshot, OCR, and system tools
- 🦑 **AI Integration** - OpenAI, Anthropic Claude, and Cursor AI integration
- 📊 **Analytics** - Test failure tracking and time series dashboards
- 🔌 **MCP Support** - Model Context Protocol for better tool integration

### Common Tasks
- **Run the Bot**: `python launcher.py`
- **Start Node Editor**: `python start_node_editor.py`
- **Run Tests**: `python tests/run_all_tests.py`
- **Setup Environment**: `python setup_env.py`

## 🛠️ Project Overview

### What is Cuttle?

Cuttle is a comprehensive PC automation system that combines:

1. **Discord Bot Interface** - Control your PC via Discord commands
2. **AI Agents** - Natural language understanding with LLM integration
3. **Visual Workflows** - Node-based pipeline editor for complex automations
4. **Automation Tools** - Extensive library of system automation capabilities
5. **Cross-Platform** - Windows native with WSL support for Linux tools

### Architecture

```
Cuttle/
├── Discord Bot        # User interface via Discord
├── AI Agent           # Natural language processing
├── Tool Manager       # Unified tool interface (MCP or Legacy)
├── Node Editor        # Visual workflow builder
├── Web API            # REST API for web integrations
└── Automation Tools   # Process, window, input, screenshot, OCR, etc.
```

### Use Cases

- **Game Development** - Automate Unity workflows, take screenshots, QA testing
- **Development Tools** - Launch IDEs, run builds, manage projects
- **System Automation** - Process management, window control, scheduled tasks
- **AI Assistance** - Multi-step AI reasoning with tool execution
- **Remote Control** - Control your PC via Discord from anywhere

## 📦 Components

### Core Systems
- **Bot** - Discord integration and command handling
- **AI Agent** - LLM integration with tool calling
- **Tool Manager** - Unified interface for all automation tools
- **MCP Integration** - Model Context Protocol for isolated tool execution
- **RAG System** - Context retrieval from Discord history

### Tools
- **Process Management** - Launch, monitor, and kill processes
- **Window Control** - Find, focus, resize, and manipulate windows
- **Input Automation** - Keyboard and mouse control
- **Screenshot** - Fast multi-monitor screenshot capture
- **OCR** - Text extraction from images and windows
- **Windows Integration** - Deep Windows system integration

### Web Features
- **Node Editor** - Visual pipeline builder (port 8080)
- **Web Chat** - Web-based chat interface (port 5000)
- **API Endpoints** - REST API for external integrations

## 🔗 Related Files

### Configuration
- `.env` - Environment variables (API keys, tokens)
- `bot_config.json` - Bot configuration
- `requirements/` - Python dependencies (requirements.txt, requirements-mcp.txt, requirements-wsl.txt)

### Key Scripts
- `launcher.py` - Main launcher with menu interface
- `bot_mcp.py` - MCP-enabled Discord bot
- `start_node_editor.py` - Launch node editor
- `web_chat_api.py` - Web API server

## 📝 Contributing

When contributing to Cuttle:
1. Follow the existing code structure
2. Add tests for new features
3. Update relevant documentation
4. Test with both MCP and legacy modes

## 🐛 Troubleshooting

### Common Issues
- **Dependencies missing**: Run `pip install -r requirements/requirements.txt`
- **Bot won't start**: Check `.env` file exists and has valid tokens
- **MCP errors**: Try legacy mode or run `python setup_mcp.py`
- **WSL issues**: See [WSL Setup Guide](setup/WSL_CURSOR_AGENT_SETUP.md)

### Getting Help
1. Check the relevant guide in this documentation
2. Review test logs: `python tests/run_all_tests.py`
3. Enable debug mode: `python launcher_debug.py`
4. Check console output for detailed error messages

## 📄 License

Part of the Cuttle project. See main project license.

---

**Last Updated**: September 2025  
**Project**: Cuttle - PC Automation Assistant  
**Status**: Active Development

For questions or issues, refer to the specific documentation files listed above.
