---
name: generate-3d
title: generate-3d
description: Generate a textured 3D GLB from a prompt or reference image (ComfyUI + TRELLIS.2)
execute: prompt
---

# Generate a 3D asset (ComfyUI + TRELLIS.2)

Follow the project skill **comfyui-trellis2**
([`.cursor/skills/comfyui-trellis2/SKILL.md`](file:////path/to/Cuttle/.cursor/skills/comfyui-trellis2/SKILL.md)).

## Do this

1. Call `comfyui_status`. If not installed, tell the user the install script is `.cuttle/scripts/install-comfyui-trellis2.ps1`.
2. If DINOv3 is missing, stop and ask them to accept the gated Hugging Face license, then `huggingface-cli login`.
3. Start ComfyUI if needed (`comfyui_start` or the `comfyui.start` action).
4. **Reference image:** `comfyui_generate_3d` with that path. Default workflow `MeshWithTexturing_LowPoly.json` (RTX 3080 10GB).
5. **Text prompt:** generate or obtain a clean concept image first (ComfyUI text-to-image, or a user upload), save it, then run step 4. TRELLIS.2 is image-to-3D, not native text-to-3D.
6. Return the GLB path under `F:/AI/ComfyUI-Trellis/output/`.

Do not restart Flask just to generate an asset. Do not `taskkill` Flask / the daemon.
