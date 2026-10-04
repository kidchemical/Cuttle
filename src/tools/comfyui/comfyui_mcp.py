#!/usr/bin/env python3
"""Standalone stdio MCP server for ComfyUI / TRELLIS.2.

Opt-in guest harness tool server; configure it in the guest MCP settings.
Cuttle does not host an MCP server. See src/tools/TOOLS_README.md for setup.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_env = _SRC / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env, override=False)
    except ImportError:
        pass

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsRequest, ListToolsResult, TextContent, Tool

from tools.comfyui.comfyui_client import (
    ComfyUIError,
    comfyui_generate_3d,
    comfyui_list_outputs,
    comfyui_list_workflows,
    comfyui_start,
    comfyui_status,
    comfyui_stop,
    comfyui_upload_image,
)

server = Server("comfyui-trellis2")


def _ok(payload: object) -> CallToolResult:
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=False)


def _err(message: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=f"Error: {message}")], isError=True)


@server.list_tools()
async def list_tools(request: ListToolsRequest | None = None) -> ListToolsResult:
    return ListToolsResult(
        tools=[
            Tool(
                name="comfyui_status",
                description="Check whether local ComfyUI + TRELLIS.2 are installed and running (port 8188).",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="comfyui_start",
                description="Start local ComfyUI in low-VRAM mode if it is not already running.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "wait_seconds": {"type": "integer", "default": 90},
                    },
                },
            ),
            Tool(
                name="comfyui_stop",
                description="Interrupt the current ComfyUI job and stop the local server.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="comfyui_list_workflows",
                description="List TRELLIS.2 / ComfyUI workflow JSON files (image-to-3D, texturing, low-poly).",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="comfyui_upload_image",
                description="Copy a local reference image into ComfyUI's input folder.",
                inputSchema={
                    "type": "object",
                    "properties": {"image_path": {"type": "string"}},
                    "required": ["image_path"],
                },
            ),
            Tool(
                name="comfyui_generate_3d",
                description=(
                    "Generate a textured 3D asset (GLB) from a reference image via TRELLIS.2. "
                    "Text-to-3D: first generate/save a concept image, then pass that path here. "
                    "Default workflow is MeshWithTexturing_LowPoly; choose for your own hardware."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "image_path": {"type": "string"},
                        "workflow": {"type": "string", "description": "Workflow filename, e.g. MeshOnly_LowPoly.json"},
                        "asset_name": {"type": "string"},
                        "timeout_sec": {"type": "integer", "default": 900},
                    },
                    "required": ["image_path"],
                },
            ),
            Tool(
                name="comfyui_list_outputs",
                description="List recent ComfyUI outputs (GLB/PNG) newest first.",
                inputSchema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 20}},
                },
            ),
        ]
    )


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    args = arguments or {}
    try:
        if name == "comfyui_status":
            return _ok(comfyui_status())
        if name == "comfyui_start":
            return _ok(comfyui_start(wait_seconds=int(args.get("wait_seconds") or 90)))
        if name == "comfyui_stop":
            return _ok(comfyui_stop())
        if name == "comfyui_list_workflows":
            return _ok(comfyui_list_workflows())
        if name == "comfyui_upload_image":
            return _ok(comfyui_upload_image(str(args.get("image_path") or "")))
        if name == "comfyui_generate_3d":
            return _ok(
                comfyui_generate_3d(
                    image_path=str(args.get("image_path") or ""),
                    workflow=args.get("workflow"),
                    asset_name=args.get("asset_name"),
                    timeout_sec=int(args.get("timeout_sec") or 900),
                )
            )
        if name == "comfyui_list_outputs":
            return _ok(comfyui_list_outputs(limit=int(args.get("limit") or 20)))
        return _err(f"Unknown tool: {name}")
    except ComfyUIError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="comfyui-trellis2",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
