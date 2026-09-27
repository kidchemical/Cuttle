# ComfyUI + TRELLIS.2 (local 3D assets)

Local install lives **outside** the Cuttle git repo:

- Root: `F:\AI\ComfyUI-Trellis` (Python 3.11 + Torch 2.8 cu128 — required for TRELLIS.2 wheels)
- Spare generic UI: `F:\AI\ComfyUI_windows_portable` (Python 3.13, not used for Trellis)
- UI: `http://127.0.0.1:8188`
- Low-VRAM launcher: `start_lowvram.bat`
- Reinstall / repair: `.cuttle/scripts/install-comfyui-trellis-py311.ps1`

## Why MCP

Cuttle agents and Cursor/Codex in other workspaces call ComfyUI through MCP tools (`comfyui_status`, `comfyui_start`, `comfyui_generate_3d`, …). The engine is still ComfyUI on port 8188; MCP is only the tool socket.

- Cuttle workspace: tools are also registered on the built-in `cuttle` MCP server.
- Other workspaces (Unity / Escape Purgatory): `comfyui` MCP in `%USERPROFILE%\.cursor\mcp.json`.

## GPU reality (this PC)

RTX 3080 **10 GB**. Official TRELLIS.2-4B wants ~24 GB. Use:

- `--lowvram --disable-pinned-memory` (already in the launcher)
- Workflows: `MeshWithTexturing_LowPoly.json` or `MeshOnly_LowPoly.json`
- Resolution 512, close Unity/games while generating

## Hugging Face gate

TRELLIS.2 needs **facebook/dinov3-vitl16-pretrain-lvd1689m**. Accept the license, then:

```powershell
F:\AI\ComfyUI-Trellis\.venv\Scripts\python.exe -m pip install huggingface_hub
F:\AI\ComfyUI-Trellis\.venv\Scripts\python.exe -m huggingface_hub.commands.huggingface_cli login
F:\AI\ComfyUI-Trellis\.venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('facebook/dinov3-vitl16-pretrain-lvd1689m', local_dir=r'F:\AI\ComfyUI-Trellis\models\facebook\dinov3-vitl16-pretrain-lvd1689m')"
```

## Prompt vs image

TRELLIS.2 is **image-to-3D**. Text prompting = make a concept image, then TRELLIS. Reference images (clean subject, background removed) work best.

## Outputs

GLBs land in `F:\AI\ComfyUI-Trellis\output\`. Drop into Unity as usual.
