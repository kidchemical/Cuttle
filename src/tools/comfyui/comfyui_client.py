"""Talk to a local ComfyUI server (TRELLIS.2 image-to-3D + texturing).

ComfyUI lives outside this repo (default ``F:\\AI\\ComfyUI_windows_portable``).
Cuttle does not vendor the 2GB+ portable install or the ~20GB model weights.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

DEFAULT_ROOT = Path(r"F:\AI\ComfyUI-Trellis")
DEFAULT_URL = "http://127.0.0.1:8188"
CONTROL_AFTER = {"fixed", "increment", "decrement", "randomize"}
SKIP_NODE_TYPES = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode"}

# RTX 3080 10GB: prefer the low-poly textured workflow.
DEFAULT_WORKFLOW = "MeshWithTexturing_LowPoly.json"


class ComfyUIError(Exception):
    """Raised when ComfyUI is missing, down, or a job fails."""


def _root() -> Path:
    raw = (os.environ.get("COMFYUI_ROOT") or "").strip()
    return Path(raw) if raw else DEFAULT_ROOT


def _url() -> str:
    return (os.environ.get("COMFYUI_URL") or DEFAULT_URL).rstrip("/")


def _comfy_dir() -> Path:
    root = _root()
    nested = root / "ComfyUI"
    return nested if nested.is_dir() else root


def _python() -> Path:
    root = _root()
    for candidate in (
        root / "python_embeded" / "python.exe",
        root / ".venv" / "Scripts" / "python.exe",
        root / "venv" / "Scripts" / "python.exe",
        root / "python.exe",
        root / ".venv" / "bin" / "python3",
        root / ".venv" / "bin" / "python",
        root / "venv" / "bin" / "python3",
    ):
        if candidate.is_file():
            return candidate
    return root / "python_embeded" / "python.exe"


def _start_bat() -> Path:
    root = _root()
    for candidate in (
        root / "run_nvidia_gpu_lowvram.bat",
        root / "run_nvidia_gpu.bat",
        root / "start_lowvram.bat",
    ):
        if candidate.is_file():
            return candidate
    return root / "run_nvidia_gpu.bat"


def _input_dir() -> Path:
    return _comfy_dir() / "input"


def _output_dir() -> Path:
    extra = (os.environ.get("COMFYUI_OUTPUT_DIR") or "").strip()
    if extra:
        return Path(extra)
    return _comfy_dir() / "output"


def _pid_file() -> Path:
    path = _root() / "user" / "cuttle_comfyui.pid"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _log_file() -> Path:
    path = _root() / "user" / "cuttle_comfyui.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def is_running(timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"{_url()}/system_stats", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def comfyui_status() -> Dict[str, Any]:
    root = _root()
    installed = (_python().is_file() and (_comfy_dir() / "main.py").is_file())
    trellis = _comfy_dir() / "custom_nodes" / "ComfyUI-Trellis2"
    dinov3 = _comfy_dir() / "models" / "facebook" / "dinov3-vitl16-pretrain-lvd1689m"
    running = is_running()
    queue: Dict[str, Any] = {}
    if running:
        try:
            queue = requests.get(f"{_url()}/queue", timeout=5).json()
        except Exception as e:
            queue = {"error": str(e)}
    return {
        "installed": installed,
        "running": running,
        "url": _url(),
        "root": str(root),
        "trellis2_nodes": trellis.is_dir(),
        "dinov3_present": any(dinov3.glob("*")) if dinov3.is_dir() else False,
        "output_dir": str(_output_dir()),
        "queue": queue,
        "gpu_note": (
            "This machine is an RTX 3080 10GB. Use MeshWithTexturing_LowPoly / MeshOnly_LowPoly, "
            "512 resolution, and --lowvram. Full TRELLIS.2-4B at 1024+ will OOM."
        ),
    }


def comfyui_start(wait_seconds: int = 90) -> Dict[str, Any]:
    if is_running():
        return {"started": False, "already_running": True, "url": _url(), **comfyui_status()}
    if not _start_bat().is_file():
        raise ComfyUIError(
            f"ComfyUI is not installed at {_root()}. Run .cuttle/scripts/install-comfyui-trellis2.ps1"
        )
    log = _log_file()
    creation = 0
    if os.name == "nt":
        creation = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    with open(log, "ab") as logf:
        logf.write(f"\n--- start {time.strftime('%Y-%m-%dT%H:%M:%S')} ---\n".encode("utf-8"))
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(_start_bat())],
            cwd=str(_root()),
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=creation,
            close_fds=True,
        )
    _pid_file().write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + max(10, int(wait_seconds))
    while time.time() < deadline:
        if is_running():
            return {"started": True, "pid": proc.pid, "url": _url(), "log": str(log)}
        time.sleep(1.5)
    raise ComfyUIError(
        f"ComfyUI did not become ready on {_url()} within {wait_seconds}s. See {log}"
    )


def comfyui_stop() -> Dict[str, Any]:
    """Ask ComfyUI to interrupt, then kill the portable python if it is still up."""
    interrupted = False
    try:
        requests.post(f"{_url()}/interrupt", timeout=3)
        interrupted = True
    except requests.RequestException:
        pass
    pid_txt = _pid_file()
    killed = False
    if pid_txt.is_file():
        try:
            pid = int(pid_txt.read_text(encoding="utf-8").strip())
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                killed = True
        except (ValueError, OSError, subprocess.TimeoutExpired):
            pass
        try:
            pid_txt.unlink()
        except OSError:
            pass
    # Wait until /system_stats fails
    for _ in range(20):
        if not is_running():
            break
        time.sleep(0.5)
    return {"interrupted": interrupted, "killed": killed, "running": is_running()}


def comfyui_list_workflows() -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    seen = set()
    roots = [
        _comfy_dir() / "custom_nodes" / "ComfyUI-Trellis2" / "example_workflows",
        _comfy_dir() / "user" / "default" / "workflows",
        _comfy_dir() / "workflows",
    ]
    for folder in roots:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            key = path.name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append({"name": path.name, "path": str(path)})
    return found


def _resolve_workflow(name: Optional[str]) -> Path:
    wanted = (name or DEFAULT_WORKFLOW).strip()
    candidate = Path(wanted)
    if candidate.is_file():
        return candidate
    for item in comfyui_list_workflows():
        if item["name"].lower() == Path(wanted).name.lower():
            return Path(item["path"])
    names = ", ".join(x["name"] for x in comfyui_list_workflows()) or "(none — install ComfyUI-Trellis2 first)"
    raise ComfyUIError(f"Workflow {wanted!r} not found. Available: {names}")


def comfyui_upload_image(image_path: str) -> Dict[str, Any]:
    src = Path(image_path)
    if not src.is_file():
        raise ComfyUIError(f"Image not found: {image_path}")
    dest_dir = _input_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    if is_running():
        with open(dest, "rb") as f:
            r = requests.post(
                f"{_url()}/upload/image",
                files={"image": (dest.name, f, "application/octet-stream")},
                data={"overwrite": "true"},
                timeout=60,
            )
        if r.status_code >= 400:
            raise ComfyUIError(f"ComfyUI upload failed HTTP {r.status_code}: {r.text[:400]}")
        return {"filename": dest.name, "path": str(dest), "upload": r.json()}
    return {"filename": dest.name, "path": str(dest), "upload": None}


def comfyui_list_outputs(limit: int = 20) -> List[Dict[str, Any]]:
    out = _output_dir()
    if not out.is_dir():
        return []
    files = []
    for path in out.rglob("*"):
        if path.suffix.lower() in {".glb", ".gltf", ".obj", ".png", ".jpg", ".jpeg", ".webp"}:
            st = path.stat()
            files.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "suffix": path.suffix.lower(),
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                }
            )
    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files[: max(1, int(limit))]


def _object_info() -> Dict[str, Any]:
    r = requests.get(f"{_url()}/object_info", timeout=30)
    r.raise_for_status()
    return r.json()


def _widget_input_names(class_info: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for section in ("required", "optional"):
        block = (class_info.get("input") or {}).get(section) or {}
        for name, spec in block.items():
            type0 = spec[0] if isinstance(spec, (list, tuple)) and spec else spec
            if isinstance(type0, list):
                names.append(name)
            elif isinstance(type0, str) and type0.upper() in {"INT", "FLOAT", "STRING", "BOOLEAN"}:
                names.append(name)
    return names


def ui_workflow_to_prompt(ui: Dict[str, Any], object_info: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a ComfyUI UI graph (nodes/links) into the /prompt API format."""
    if isinstance(ui.get("prompt"), dict):
        return ui["prompt"]
    nodes = ui.get("nodes")
    if not nodes and all(isinstance(v, dict) and "class_type" in v for v in ui.values() if isinstance(v, dict)):
        return {str(k): v for k, v in ui.items() if isinstance(v, dict) and "class_type" in v}

    link_map: Dict[Tuple[int, int], List[Any]] = {}
    for link in ui.get("links") or []:
        if not isinstance(link, (list, tuple)) or len(link) < 5:
            continue
        _lid, src, src_slot, dst, dst_slot = link[:5]
        link_map[(int(dst), int(dst_slot))] = [str(src), int(src_slot)]

    prompt: Dict[str, Any] = {}
    for node in nodes or []:
        ntype = node.get("type") or node.get("class_type")
        if not ntype or ntype in SKIP_NODE_TYPES:
            continue
        if int(node.get("mode") or 0) == 4:
            continue
        nid = str(node["id"])
        class_info = object_info.get(ntype) or {}
        widget_names = _widget_input_names(class_info)
        inputs: Dict[str, Any] = {}
        linked_names = set()
        for slot, inp in enumerate(node.get("inputs") or []):
            name = inp.get("name")
            if not name:
                continue
            if inp.get("link") is not None:
                mapped = link_map.get((int(node["id"]), slot))
                if mapped:
                    inputs[name] = mapped
                    linked_names.add(name)
        widgets = list(node.get("widgets_values") or [])
        wi = 0
        for name in widget_names:
            if name in linked_names:
                continue
            if wi >= len(widgets):
                break
            value = widgets[wi]
            wi += 1
            if wi < len(widgets) and isinstance(widgets[wi], str) and widgets[wi].lower() in CONTROL_AFTER:
                wi += 1
            inputs[name] = value
        prompt[nid] = {"class_type": ntype, "inputs": inputs}
    if not prompt:
        raise ComfyUIError("Workflow converted to an empty prompt (muted nodes or unknown format).")
    return prompt


def _patch_workflow(ui: Dict[str, Any], image_name: str, asset_name: str, low_vram: bool) -> None:
    for node in ui.get("nodes") or []:
        ntype = node.get("type")
        widgets = node.get("widgets_values")
        if not isinstance(widgets, list):
            continue
        if ntype in {"Trellis2LoadImageWithTransparency", "LoadImage", "LoadImageMask"}:
            if widgets:
                widgets[0] = image_name
        elif ntype in {"PrimitiveString"} and str(node.get("title") or "").lower() in {"name", "filename", "asset"}:
            if widgets:
                widgets[0] = asset_name
        elif ntype == "Trellis2LoadModel" and low_vram:
            # visualbruno widgets: model, attn, device, low_vram, ...
            if len(widgets) >= 4 and isinstance(widgets[3], bool):
                widgets[3] = True
            for i, val in enumerate(widgets):
                if isinstance(val, str) and val.lower() in {"flash_attn", "flash_attn_3"}:
                    # sdpa is more forgiving on 10GB cards if flash-attn wheels are missing
                    widgets[i] = "sdpa"


def _queue_prompt(prompt: Dict[str, Any], client_id: str) -> str:
    body = {"prompt": prompt, "client_id": client_id}
    r = requests.post(f"{_url()}/prompt", json=body, timeout=60)
    if r.status_code >= 400:
        raise ComfyUIError(f"ComfyUI rejected the prompt HTTP {r.status_code}: {r.text[:800]}")
    data = r.json()
    if data.get("error"):
        raise ComfyUIError(f"ComfyUI prompt error: {data.get('error')} {data.get('node_errors')}")
    pid = data.get("prompt_id")
    if not pid:
        raise ComfyUIError(f"ComfyUI did not return prompt_id: {data}")
    return str(pid)


def _wait_history(prompt_id: str, timeout_sec: int) -> Dict[str, Any]:
    deadline = time.time() + max(30, int(timeout_sec))
    last = {}
    while time.time() < deadline:
        try:
            r = requests.get(f"{_url()}/history/{prompt_id}", timeout=10)
            if r.status_code == 200:
                last = r.json() or {}
                entry = last.get(prompt_id) or (last if "outputs" in last else None)
                if entry and entry.get("status", {}).get("completed"):
                    return entry
                if entry and entry.get("outputs"):
                    return entry
                err = (entry or {}).get("status", {}).get("status_str")
                if err == "error":
                    raise ComfyUIError(f"ComfyUI job failed: {json.dumps(entry.get('status'), default=str)[:800]}")
        except ComfyUIError:
            raise
        except requests.RequestException:
            pass
        time.sleep(2)
    raise ComfyUIError(f"Timed out after {timeout_sec}s waiting for prompt {prompt_id}")


def comfyui_generate_3d(
    image_path: str,
    workflow: Optional[str] = None,
    asset_name: Optional[str] = None,
    timeout_sec: int = 900,
    start_if_needed: bool = True,
    low_vram: bool = True,
) -> Dict[str, Any]:
    """Image-to-3D (and texture, if the workflow includes it). Returns output GLB paths."""
    status = comfyui_status()
    if not status["installed"]:
        raise ComfyUIError(f"ComfyUI is not installed at {status['root']}")
    if not status["trellis2_nodes"]:
        raise ComfyUIError("ComfyUI-Trellis2 custom nodes are missing. Re-run the install script.")
    if start_if_needed and not status["running"]:
        comfyui_start()
    elif not is_running():
        raise ComfyUIError("ComfyUI is not running. Call comfyui_start first.")

    uploaded = comfyui_upload_image(image_path)
    image_name = uploaded["filename"]
    name = (asset_name or Path(image_path).stem or "asset").replace(" ", "_")
    wf_path = _resolve_workflow(workflow)
    ui = json.loads(wf_path.read_text(encoding="utf-8"))
    _patch_workflow(ui, image_name, name, low_vram=low_vram)
    info = _object_info()
    prompt = ui_workflow_to_prompt(ui, info)
    before = {p["path"] for p in comfyui_list_outputs(limit=50)}
    prompt_id = _queue_prompt(prompt, client_id=f"cuttle-{uuid.uuid4().hex[:10]}")
    history = _wait_history(prompt_id, timeout_sec=timeout_sec)
    after = comfyui_list_outputs(limit=50)
    new_files = [p for p in after if p["path"] not in before]
    glbs = [p for p in new_files if p["suffix"] == ".glb"]
    return {
        "prompt_id": prompt_id,
        "workflow": str(wf_path),
        "image": image_name,
        "asset_name": name,
        "outputs": new_files or after[:5],
        "glb": glbs[0]["path"] if glbs else None,
        "history_status": history.get("status"),
        "url": _url(),
    }
