# Cuttle tools (live)

Guest agent CLIs own OS automation (browser, filesystem, shell). Cuttle does not host an MCP tool server.

This folder has **ComfyUI**, **Govee**, **OCR** (chat attachment fallback), and **web search**.

Cursor Agent CLI / Codex may still launch `comfyui/comfyui_mcp.py` from *their* own MCP config. That is the guest harness, not a Cuttle MCP product.

## Optional ComfyUI guest integration

The retained `tools.comfyui` Python exports and standalone
`comfyui/comfyui_mcp.py` wrapper are opt-in. Set `COMFYUI_ROOT` to an external
installation. `COMFYUI_URL` defaults to `http://127.0.0.1:8188`;
`COMFYUI_OUTPUT_DIR` can override the installation's output directory.
Unconfigured status reports `configured: false`; file operations require a root.
Workflow and VRAM choices depend on your hardware; no GPU is assumed.

For a guest MCP, create a separate environment under your project's ignored
`temp/` (or another explicitly chosen environment) and install `mcp` and
`requests` there. Configure your guest harness to launch that environment's
Python with the absolute path to `src/tools/comfyui/comfyui_mcp.py`, and supply
`COMFYUI_ROOT` (plus optional URL/output overrides) in its environment. For example:

```bash
python3 -m venv temp/comfyui-mcp-venv
temp/comfyui-mcp-venv/bin/python -m pip install mcp requests
COMFYUI_ROOT=/path/to/ComfyUI temp/comfyui-mcp-venv/bin/python src/tools/comfyui/comfyui_mcp.py
```

This starts the guest stdio server only when invoked; Cuttle never hosts it or
starts ComfyUI at daemon boot. TRELLIS commands, actions, install scripts and
machine notes are personal opt-ins rather than shipped default configuration.
