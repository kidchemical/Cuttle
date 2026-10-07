"""
Shared chat media staging for markdown images / video in Cuttle bubbles.

Agents (and auto-rewrite) copy files into ``<home>/output/shared/`` so LAN/phone
clients can preview via ``/output/shared/<id>.ext``. Staged copies expire after
``SHARED_MEDIA_TTL_DAYS`` (default 7) and are purged on daemon / Flask /
Electron host start.

Videos also get a mid-frame ``*.poster.jpg`` sidecar (ffmpeg) for chat thumbs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".ogg", ".ogv"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Markdown image + <media src="..."/> local-path rewrite
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_MD_IMAGE_TITLE_RE = re.compile(r"""\s+("([^"]*)"|'([^']*)')\s*$""")
_MEDIA_SRC_RE = re.compile(
    r"(<media\b[^>]*?\bsrc\s*=\s*[\"'])([^\"']+)([\"'][^>]*?/?>)",
    re.IGNORECASE,
)

DEFAULT_TTL_DAYS = 7
MAX_STAGE_BYTES = 200 * 1024 * 1024  # 200 MiB
POSTER_SUFFIX = ".poster.jpg"
META_SUFFIX = ".meta.json"

_FFMPEG_CACHE: Optional[str] = None
_FFPROBE_CACHE: Optional[str] = None


def shared_media_root() -> Path:
    from core.runtime_paths import output_dir

    return (output_dir() / "shared").resolve()


def _default_project_root() -> Path:
    # src/api/shared_media.py → src → repo root
    return Path(__file__).resolve().parents[2]


def ttl_days() -> int:
    raw = (os.getenv("CUTTLE_SHARED_MEDIA_TTL_DAYS") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return DEFAULT_TTL_DAYS


def media_kind(path_or_name: str) -> Optional[str]:
    ext = Path(path_or_name).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    # poster sidecars are images for serving, but named *.poster.jpg
    name = Path(path_or_name).name.lower()
    if name.endswith(POSTER_SUFFIX):
        return "image"
    return None


def public_url(filename: str) -> str:
    name = Path(filename).name
    return f"/output/shared/{name}"


def poster_filename_for(video_filename: str) -> str:
    """``clip-abc123.mp4`` → ``clip-abc123.poster.jpg``."""
    stem = Path(video_filename).name
    p = Path(stem)
    if p.suffix.lower() in VIDEO_EXTS:
        return f"{p.stem}{POSTER_SUFFIX}"
    return f"{stem}{POSTER_SUFFIX}"


def meta_filename_for(media_filename: str) -> str:
    """``clip-abc123.mp4`` → ``clip-abc123.meta.json``."""
    stem = Path(media_filename).name
    p = Path(stem)
    if p.suffix.lower() in MEDIA_EXTS or p.name.lower().endswith(POSTER_SUFFIX):
        # For posters use the video stem; for media drop the extension
        if p.name.lower().endswith(POSTER_SUFFIX):
            return f"{p.name[: -len(POSTER_SUFFIX)]}{META_SUFFIX}"
        return f"{p.stem}{META_SUFFIX}"
    return f"{stem}{META_SUFFIX}"


def write_stage_meta(
    staged_filename: str,
    *,
    original_path: str | Path | None,
    original_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist original source info next to a staged shared media file."""
    shared = shared_media_root()
    shared.mkdir(parents=True, exist_ok=True)
    name = Path(staged_filename).name
    meta_path = shared / meta_filename_for(name)
    orig_display = str(original_path).replace("\\", "/") if original_path else ""
    payload: Dict[str, Any] = {
        "staged_filename": name,
        "staged_url": public_url(name),
        "original_path": orig_display or None,
        "original_name": original_name or (Path(orig_display).name if orig_display else name),
        "staged_at": time.time(),
    }
    if extra:
        payload.update(extra)
    try:
        import json

        meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "path": str(meta_path), "meta": payload}


def read_stage_meta(
    url_or_filename: str,
) -> Dict[str, Any]:
    """Load sidecar meta for a staged ``/output/shared/…`` URL or filename."""
    import json

    raw = _unwrap_md_dest(url_or_filename).split("?")[0].split("#")[0]
    name = Path(raw).name
    if not name:
        return {"success": False, "error": "filename required"}

    shared = shared_media_root()
    if name.lower().endswith(POSTER_SUFFIX):
        stem = name[: -len(POSTER_SUFFIX)]
        meta_path = shared / f"{stem}{META_SUFFIX}"
    else:
        meta_path = shared / meta_filename_for(name)

    if not meta_path.is_file():
        return {
            "success": True,
            "meta": {
                "staged_filename": name,
                "staged_url": public_url(name),
                "original_path": None,
                "original_name": name,
            },
            "cached": False,
        }
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"success": False, "error": str(e)}
    if not isinstance(data, dict):
        return {"success": False, "error": "invalid meta"}
    return {"success": True, "meta": data, "path": str(meta_path), "cached": True}


def poster_url_for(video_url: str) -> Optional[str]:
    """Map a hosted video URL to its conventional poster URL."""
    u = _unwrap_md_dest(video_url).split("?")[0].split("#")[0]
    if not u.startswith("/output/shared/"):
        return None
    name = Path(u).name
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        return None
    return public_url(poster_filename_for(name))


def _which_tool(names: List[str]) -> Optional[str]:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def find_ffmpeg() -> Optional[str]:
    global _FFMPEG_CACHE
    if _FFMPEG_CACHE is not None:
        return _FFMPEG_CACHE or None
    env = (os.getenv("CUTTLE_FFMPEG") or "").strip()
    if env and Path(env).is_file():
        _FFMPEG_CACHE = env
        return env
    found = _which_tool(["ffmpeg", "ffmpeg.exe"])
    _FFMPEG_CACHE = found or ""
    return found


def find_ffprobe() -> Optional[str]:
    global _FFPROBE_CACHE
    if _FFPROBE_CACHE is not None:
        return _FFPROBE_CACHE or None
    env = (os.getenv("CUTTLE_FFPROBE") or "").strip()
    if env and Path(env).is_file():
        _FFPROBE_CACHE = env
        return env
    found = _which_tool(["ffprobe", "ffprobe.exe"])
    if not found:
        ff = find_ffmpeg()
        if ff:
            sibling = Path(ff).with_name(
                "ffprobe.exe" if Path(ff).suffix.lower() == ".exe" else "ffprobe"
            )
            if sibling.is_file():
                found = str(sibling)
    _FFPROBE_CACHE = found or ""
    return found


def probe_duration_seconds(video_path: Path) -> Optional[float]:
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode != 0:
            return None
        val = float((proc.stdout or "").strip())
        if val > 0:
            return val
    except Exception:
        return None
    return None


def extract_midframe_poster(
    video_path: Path | str,
    dest_jpg: Path | str,
) -> Dict[str, Any]:
    """
    Grab a frame near the middle of ``video_path`` into ``dest_jpg`` (JPEG).
    Falls back to ~1s if duration is unknown.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"success": False, "error": "ffmpeg not found"}

    src = Path(video_path)
    dest = Path(dest_jpg)
    if not src.is_file():
        return {"success": False, "error": f"not a file: {src}"}

    duration = probe_duration_seconds(src)
    if duration and duration > 0.2:
        # Slightly before true midpoint avoids last-frame black on some clips
        seek = max(0.05, duration * 0.5)
    else:
        seek = 1.0

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{seek:.3f}",
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 32:
        err = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return {"success": False, "error": err[:400]}

    return {
        "success": True,
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "seek_seconds": seek,
        "duration_seconds": duration,
    }


def ensure_video_poster(
    video_path: Path | str,
) -> Dict[str, Any]:
    """
    Ensure a ``*.poster.jpg`` sidecar exists next to a staged (or any) video.
    Returns ``{success, poster_url, poster_filename, ...}``.
    """
    src = Path(video_path)
    try:
        src = src.resolve()
    except Exception as e:
        return {"success": False, "error": f"invalid path: {e}"}
    if not src.is_file() or media_kind(src.name) != "video":
        return {"success": False, "error": "not a video file"}

    poster_name = poster_filename_for(src.name)
    shared = shared_media_root()
    try:
        src.relative_to(shared)
        dest = shared / poster_name
    except ValueError:
        dest = src.with_name(poster_name)

    if dest.is_file() and dest.stat().st_size >= 32:
        return {
            "success": True,
            "poster_url": public_url(dest.name) if dest.parent == shared else str(dest),
            "poster_filename": dest.name,
            "path": str(dest),
            "cached": True,
        }

    result = extract_midframe_poster(src, dest)
    if not result.get("success"):
        return result
    return {
        "success": True,
        "poster_url": public_url(dest.name) if dest.parent == shared else str(dest),
        "poster_filename": dest.name,
        "path": str(dest),
        "cached": False,
        "seek_seconds": result.get("seek_seconds"),
        "duration_seconds": result.get("duration_seconds"),
        "bytes": result.get("bytes"),
    }


def ensure_poster_for_shared_url(
    url: str,
) -> Dict[str, Any]:
    """Lazy poster for ``/output/shared/<video>`` (used by serve + API)."""
    u = _unwrap_md_dest(url).split("?")[0].split("#")[0]
    if not u.startswith("/output/shared/"):
        return {"success": False, "error": "only /output/shared/ videos supported"}
    name = Path(u).name
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        return {"success": False, "error": "not a video url"}
    video_path = shared_media_root() / name
    if not video_path.is_file():
        return {"success": False, "error": "video not found"}
    return ensure_video_poster(video_path)


def ensure_poster_sidecar_file(
    poster_rel: str,
) -> Optional[Path]:
    """
    If ``shared/<stem>.poster.jpg`` is missing, try to build it from the
    matching video. Returns the poster Path when present, else None.
    """
    name = Path(poster_rel).name
    if not name.lower().endswith(POSTER_SUFFIX):
        return None
    shared = shared_media_root()
    poster_path = shared / name
    if poster_path.is_file() and poster_path.stat().st_size >= 32:
        return poster_path
    stem = name[: -len(POSTER_SUFFIX)]
    for ext in VIDEO_EXTS:
        cand = shared / f"{stem}{ext}"
        if cand.is_file():
            result = ensure_video_poster(cand)
            if result.get("success") and poster_path.is_file():
                return poster_path
            break
    return poster_path if poster_path.is_file() else None


def split_md_image_dest(raw: str) -> Tuple[str, Optional[str]]:
    """Split ``url`` / ``url "description"`` into ``(url, description_or_None)``."""
    s = str(raw or "").strip()
    if not s:
        return "", None
    m = _MD_IMAGE_TITLE_RE.search(s)
    if not m:
        return s, None
    url = s[: m.start()].strip()
    title = m.group(2) if m.group(2) is not None else m.group(3)
    return url, title


def _unwrap_md_dest(raw: str) -> str:
    u, _title = split_md_image_dest(raw)
    if u.startswith("<") and u.endswith(">"):
        u = u[1:-1].strip()
    return u


def is_already_hosted_url(url: str) -> bool:
    u = _unwrap_md_dest(url)
    if not u:
        return False
    if u.startswith("/output/"):
        return True
    if re.match(r"^https?://", u, re.I):
        return True
    return False


def looks_like_local_path(url: str) -> bool:
    u = _unwrap_md_dest(url)
    if not u or is_already_hosted_url(u):
        return False
    if re.match(r"^file:", u, re.I):
        return True
    if u.startswith("/") and not u.startswith("//"):
        return True
    if re.match(r"^[a-zA-Z]:[\\/]", u):
        return True
    if re.match(r"^/[a-zA-Z]:[\\/]", u):
        return True
    if u.startswith("\\\\") or u.startswith("//"):
        return True
    if re.match(r"^(?:\./|\.\./|src/|temp/|tmp/)", u.replace("\\", "/")):
        return True
    return False


def resolve_local_path(url: str, project_root: Optional[Path] = None) -> Optional[Path]:
    """Turn a markdown/media src into an absolute filesystem path, or None."""
    u = _unwrap_md_dest(url)
    if not u:
        return None
    root = Path(project_root) if project_root else _default_project_root()

    if re.match(r"^file:", u, re.I):
        body = re.sub(r"^file:/*", "", u, flags=re.I)
        try:
            from urllib.parse import unquote

            body = unquote(body)
        except Exception:
            pass
        if re.match(r"^[a-zA-Z]:", body):
            return Path(body)
        if body.startswith("/") and re.match(r"^/[a-zA-Z]:", body):
            return Path(body[1:])
        return Path("/" + body.lstrip("/"))

    m = re.match(r"^/([a-zA-Z]:)([\\/].*)$", u)
    if m:
        return Path(m.group(1) + m.group(2))

    if re.match(r"^[a-zA-Z]:[\\/]", u):
        return Path(u)

    if u.startswith("/") and not re.match(r"^/[a-zA-Z]:", u):
        try:
            return Path(u).resolve()
        except OSError:
            return None

    cand = (root / u).resolve()
    try:
        cand.relative_to(root.resolve())
    except ValueError:
        return None
    return cand


def stage_file(
    source: Path | str,
    *,
    preferred_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Copy a local media file into ``<home>/output/shared/``.

    Returns ``{success, url, filename, kind, path, poster_url?}`` or
    ``{success: False, error}``.
    """
    src = Path(source)
    try:
        src = src.resolve()
    except Exception as e:
        return {"success": False, "error": f"invalid path: {e}"}

    if not src.is_file():
        return {"success": False, "error": f"not a file: {src}"}

    kind = media_kind(src.name)
    if not kind:
        return {"success": False, "error": f"unsupported media type: {src.suffix}"}

    try:
        size = src.stat().st_size
    except OSError as e:
        return {"success": False, "error": str(e)}
    if size > MAX_STAGE_BYTES:
        return {
            "success": False,
            "error": f"file too large ({size} bytes; max {MAX_STAGE_BYTES})",
        }

    dest_dir = shared_media_root()
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext = src.suffix.lower() or ".bin"
    base = Path(preferred_name).stem if preferred_name else src.stem
    base = re.sub(r"[^a-zA-Z0-9._-]+", "_", base).strip("._") or "media"
    base = base[:48]
    filename = f"{base}-{uuid.uuid4().hex[:10]}{ext}"
    dest = dest_dir / filename

    try:
        shutil.copy2(src, dest)
    except OSError as e:
        return {"success": False, "error": f"copy failed: {e}"}

    out: Dict[str, Any] = {
        "success": True,
        "url": public_url(filename),
        "filename": filename,
        "kind": kind,
        "path": str(dest),
        "bytes": size,
        "original_path": str(src).replace("\\", "/"),
        "original_name": Path(preferred_name).name if preferred_name else src.name,
    }

    meta_result = write_stage_meta(
        filename,
        original_path=src,
        original_name=out["original_name"],
        extra={"kind": kind, "bytes": size},
    )
    if meta_result.get("success"):
        out["meta"] = meta_result.get("meta")

    if kind == "video":
        poster = ensure_video_poster(dest)
        if poster.get("success"):
            out["poster_url"] = poster.get("poster_url")
            out["poster_filename"] = poster.get("poster_filename")
        else:
            out["poster_error"] = poster.get("error")

    return out


def purge_expired(
    *,
    ttl: Optional[int] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Delete staged files older than TTL days (by mtime)."""
    root = shared_media_root()
    days = ttl if ttl is not None else ttl_days()
    cutoff = (now if now is not None else time.time()) - (days * 86400)
    deleted = []
    errors = []
    if not root.is_dir():
        return {"success": True, "deleted": 0, "files": [], "ttl_days": days}

    for p in root.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError as e:
            errors.append(f"{p.name}: {e}")
            continue
        if mtime > cutoff:
            continue
        try:
            p.unlink()
            deleted.append(p.name)
        except OSError as e:
            errors.append(f"{p.name}: {e}")

    return {
        "success": True,
        "deleted": len(deleted),
        "files": deleted,
        "ttl_days": days,
        "errors": errors,
    }


def rewrite_local_media_refs(
    text: str,
    *,
    project_root: Optional[Path] = None,
) -> Tuple[str, int]:
    """
    Rewrite ``![…](local-path)`` and ``<media src="local-path"/>`` to hosted URLs.
    Returns ``(new_text, staged_count)``.
    """
    if not text or not isinstance(text, str):
        return text, 0
    if "![" not in text and "<media" not in text.lower():
        return text, 0

    staged = 0
    cache: Dict[str, str] = {}

    def _stage_url(raw_url: str) -> str:
        nonlocal staged
        key = _unwrap_md_dest(raw_url)
        if not key or is_already_hosted_url(key):
            return raw_url
        if not looks_like_local_path(key):
            return raw_url
        if key in cache:
            return cache[key]
        path = resolve_local_path(key, project_root)
        if path is None or not path.is_file():
            cache[key] = raw_url
            return raw_url
        result = stage_file(path)
        if not result.get("success"):
            cache[key] = raw_url
            return raw_url
        url = result["url"]
        cache[key] = url
        staged += 1
        return url

    def _md_sub(m: re.Match) -> str:
        alt, dest = m.group(1), m.group(2)
        url, title = split_md_image_dest(dest)
        new_url = _stage_url(url)
        if title is None:
            return f"![{alt}]({new_url})"
        # Prefer double quotes; fall back to single if the desc contains ".
        if '"' not in title:
            return f'![{alt}]({new_url} "{title}")'
        safe = title.replace("'", "\\'")
        return f"![{alt}]({new_url} '{safe}')"

    out = _MD_IMAGE_RE.sub(_md_sub, text)

    def _media_sub(m: re.Match) -> str:
        prefix, src, suffix = m.group(1), m.group(2), m.group(3)
        new_src = _stage_url(src)
        return f"{prefix}{new_src}{suffix}"

    out = _MEDIA_SRC_RE.sub(_media_sub, out)
    return out, staged
