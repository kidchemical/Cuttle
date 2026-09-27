---
name: comfyui-trellis2
description: >-
  Local ComfyUI + Microsoft TRELLIS.2 for 3D mesh/texture generation from
  prompts or reference images. Use when the user wants 3D assets, GLB/GLTF
  models, image-to-3D, mesh texturing, ComfyUI, or Trellis2.
---

# ComfyUI + TRELLIS.2 (Cuttle)

## What this is

Local **ComfyUI** at `F:\AI\ComfyUI-Trellis` (Python 3.11) with **visualbruno/ComfyUI-Trellis2**.
Cuttle talks to it over HTTP (`http://127.0.0.1:8188`) via MCP tools. Do **not**
clone ComfyUI into the Cuttle git repo.

## Tools (prefer these)

`comfyui_status` → `comfyui_start` → `comfyui_generate_3d` → `comfyui_list_outputs`

Standalone MCP (other workspaces): `src/tools/comfyui/comfyui_mcp.py`.
Same functions live on Cuttle's MCP server.

## This PC

RTX 3080 **10 GB** + 32 GB RAM. Default workflow **`MeshWithTexturing_LowPoly.json`**.
Do not queue 1024/1536 or `MeshWithTexturing_HighQuality` here — it will OOM.
Close Unity while generating if VRAM is tight.

## Prompt vs reference image

TRELLIS.2 is **image-to-3D**:

1. User attached / named an image → `comfyui_generate_3d` with that path.
2. User only gave a text prompt → make or obtain a concept image first, save it,
   then call `comfyui_generate_3d`. There is no native text-to-3D node.

Best inputs: single subject, opaque background removed, ~1024px.

## Blockers

- **DINOv3 gated** on Hugging Face. If `dinov3_present` is false, ask the human
  to accept https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m and
  `huggingface-cli login`, then re-run the install script. Do not fake a mesh.
- ComfyUI missing → `.cuttle/scripts/install-comfyui-trellis2.ps1`

## Outputs

Return the `.glb` path under `F:/AI/ComfyUI-Trellis/output/`.
Jobs often take 3–10 minutes on this GPU.

## Don'ts

- Don't `taskkill` Flask / `cuttle_daemon` / Discord.
- Don't install Trellis into Cuttle's `.venv`.
- Don't start ComfyUI at daemon boot — on-demand only (GPU is shared with Unity).
