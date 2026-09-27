"""ComfyUI + TRELLIS.2 client used by Cuttle MCP and other harnesses."""

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

__all__ = [
    "ComfyUIError",
    "comfyui_generate_3d",
    "comfyui_list_outputs",
    "comfyui_list_workflows",
    "comfyui_start",
    "comfyui_status",
    "comfyui_stop",
    "comfyui_upload_image",
]
