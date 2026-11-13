"""
Vision / document pre-pass for chat attachments.

Runs before the agent (pipeline or CLI harness): describes images (Claude
vision → OpenAI vision), extracts PDF text (rasterize + vision when
pages are image-heavy) and transcribes voice clips recorded in the composer
(``api.voice_stt``). Produces a plain-text digest the rest of the stack can
use, since none of the CLI harnesses (Cursor, Codex, Muse, Hermes) accept image
input.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

StatusCb = Optional[Callable[[str], None]]

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PDF_EXTS = {".pdf"}
AUDIO_EXTS = {".webm", ".ogg", ".m4a", ".wav", ".mp3"}
#: What ``POST /api/upload`` accepts: everything this pre-pass can digest.
UPLOAD_EXTS = IMAGE_EXTS | PDF_EXTS | AUDIO_EXTS
_AUDIO_MIME_BY_EXT = {".webm": "audio/webm", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
                      ".wav": "audio/wav", ".mp3": "audio/mpeg"}
# The transcription API's own request limit is 25 MB.
MAX_AUDIO_BYTES = 24 * 1024 * 1024
MAX_TRANSCRIPT_CHARS = 12000
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES_TEXT = 20
MAX_PDF_PAGES_VISION = 8
MAX_PDF_CHARS = 12000
MAX_DESC_CHARS = 4000
MIN_TEXT_CHARS_PER_PAGE = 40

VISION_MODEL = os.getenv("CUTTLE_VISION_MODEL", "claude-haiku-4-5-20251001")
OPENAI_VISION_MODEL = os.getenv("CUTTLE_VISION_MODEL_OPENAI", "gpt-4o-mini")

DIGEST_HEADER = "[Attached content — pre-analyzed]"

VISION_PROMPT = (
    "Describe the attached image ({filename}) for an AI coding/automation agent. "
    "Include all visible text (OCR), UI labels, code, diagrams, charts, tables, "
    "error messages, and any other details needed to act on the user's request. "
    "Be concrete and complete; avoid fluff."
)


def _emit(status_cb: StatusCb, msg: str) -> None:
    if status_cb:
        try:
            status_cb(msg)
        except Exception:
            pass


def _guess_mime(filename: str, mime: Optional[str] = None) -> str:
    if mime and mime != "application/octet-stream":
        return mime
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _is_image(filename: str, mime: str) -> bool:
    ext = Path(filename).suffix.lower()
    return mime.startswith("image/") or ext in IMAGE_EXTS


def _is_pdf(filename: str, mime: str) -> bool:
    ext = Path(filename).suffix.lower()
    return mime == "application/pdf" or ext in PDF_EXTS


def _is_audio(filename: str, mime: str) -> bool:
    # Extension first: ``mimetypes`` calls a recorder's ``.webm`` a video.
    ext = Path(filename).suffix.lower()
    return ext in AUDIO_EXTS or mime.startswith("audio/")


def _read_bytes(att: Dict[str, Any]) -> bytes:
    if att.get("data") is not None:
        data = att["data"]
        return data if isinstance(data, (bytes, bytearray)) else bytes(data)
    path = att.get("path")
    if path:
        return Path(path).read_bytes()
    url = att.get("url")
    if url:
        import requests

        r = requests.get(url, timeout=60)
        r.raise_for_status()
        return r.content
    raise ValueError("attachment has no path, url, or data")


def _is_remote_url(url: Optional[str]) -> bool:
    return bool(url) and str(url).lower().startswith(("http://", "https://"))


def _normalize_image_bytes(data: bytes, mime: str) -> tuple:
    """Coerce to a media type both vision APIs accept (jpeg/png/gif/webp)."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image too large ({len(data)} bytes)")
    if mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        return data, mime
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    except Exception:
        return data, "image/png"


def _short_error(exc: Exception) -> str:
    from api.model_providers.cache import public_error
    # Preserve actionable, known categories without echoing provider bodies.
    if "credit balance is too low" in str(exc).lower():
        return "credit balance is too low"
    return public_error(exc, "Vision request")


def _describe_image(provider, model, *, data=None, url=None, mime="image/png", filename="image"):
    from api.model_providers.service import generate_text
    from api.model_providers.types import TextRequest, ImageInput
    if data is None and not _is_remote_url(url):
        raise ValueError("image data required")
    if data is not None:
        data, mime = _normalize_image_bytes(data, mime)
    result = generate_text(provider, TextRequest(VISION_PROMPT.format(filename=filename),
                                               model=model, max_tokens=1024,
                                               images=(ImageInput(data, mime, url if data is None else None),)),
                           usage_task="vision")
    if result is None:
        raise RuntimeError("Vision provider API key missing or model unsupported")
    return result.text.strip()


def _describe_image_claude(**kwargs):
    return _describe_image("anthropic", VISION_MODEL, **kwargs)


def _describe_image_openai(**kwargs):
    return _describe_image("openai", OPENAI_VISION_MODEL, **kwargs)


VISION_PROVIDERS = (
    ("Claude", _describe_image_claude),
    ("OpenAI", _describe_image_openai),
)


def _describe_image_via_providers(
    *,
    data: Optional[bytes] = None,
    url: Optional[str] = None,
    mime: str = "image/png",
    filename: str = "image",
) -> str:
    """
    Try each vision provider in turn.

    A provider that is out of credit / missing a key must not degrade into a
    silent empty result — the agent then confidently tells the user no image
    arrived. Failures are reported inline instead.
    """
    errors: List[str] = []
    for label, fn in VISION_PROVIDERS:
        try:
            out = (fn(data=data, url=url, mime=mime, filename=filename) or "").strip()
            if out:
                return out
            errors.append(f"{label}: empty response")
        except Exception as e:
            errors.append(f"{label}: {_short_error(e)}")

    detail = "; ".join(errors) or "no vision provider configured"
    return f"(image not analyzed — {detail})"


def describe_image(att: Dict[str, Any]) -> str:
    filename = att.get("filename") or "image"
    mime = _guess_mime(filename, att.get("mime"))
    url = att.get("url")
    data = None
    try:
        data = _read_bytes(att)
    except Exception as e:
        if not _is_remote_url(url):
            return f"(could not read attachment: {_short_error(e)})"
    return _describe_image_via_providers(
        data=data, url=url, mime=mime, filename=filename
    )[:MAX_DESC_CHARS]


def extract_pdf(att: Dict[str, Any], status_cb: StatusCb = None) -> str:
    filename = att.get("filename") or "document.pdf"
    data = _read_bytes(att)
    if len(data) > MAX_PDF_BYTES:
        return f"(PDF too large: {len(data)} bytes; max {MAX_PDF_BYTES})"

    try:
        import pypdfium2 as pdfium
    except ImportError:
        return "(PDF support requires pypdfium2 — pip install pypdfium2)"

    try:
        doc = pdfium.PdfDocument(data)
    except Exception as e:
        return f"(failed to open PDF: {e})"

    try:
        n_pages = len(doc)
        text_parts: List[str] = []
        sparse_pages: List[int] = []

        for i in range(n_pages):
            if i >= MAX_PDF_PAGES_TEXT:
                text_parts.append(f"[… truncated after {MAX_PDF_PAGES_TEXT} pages; PDF has {n_pages} total]")
                break
            page = doc[i]
            textpage = page.get_textpage()
            try:
                t = (textpage.get_text_bounded() or "").strip()
            finally:
                textpage.close()
                page.close()
            if len(t) < MIN_TEXT_CHARS_PER_PAGE:
                sparse_pages.append(i)
            if t:
                text_parts.append(f"[Page {i + 1}]\n{t}")

        combined = "\n\n".join(text_parts).strip()
        # Only rasterize+vision when text extraction is thin (scanned / image PDFs).
        need_vision = (len(combined) < 100) or (
            bool(sparse_pages)
            and len(sparse_pages) >= max(1, (n_pages + 1) // 2)
            and len(combined) < 500
        )

        vision_notes: List[str] = []
        if need_vision:
            _emit(status_cb, f"Vision-scanning PDF pages ({filename})...")
            pages_to_scan = sparse_pages[:MAX_PDF_PAGES_VISION] if sparse_pages else list(range(min(n_pages, MAX_PDF_PAGES_VISION)))
            for i in pages_to_scan:
                try:
                    page = doc[i]
                    try:
                        bitmap = page.render(scale=1.5)
                        buf = io.BytesIO()
                        bitmap.to_pil().convert("RGB").save(buf, format="PNG")
                        png = buf.getvalue()
                    finally:
                        page.close()
                    desc = _describe_image_via_providers(
                        data=png,
                        mime="image/png",
                        filename=f"{filename} p.{i + 1}",
                    )
                    vision_notes.append(f"[Page {i + 1} — vision]\n{desc}")
                except Exception as e:
                    vision_notes.append(f"[Page {i + 1} — vision failed: {e}]")

        chunks = []
        if combined:
            chunks.append(combined[:MAX_PDF_CHARS])
        if vision_notes:
            chunks.append("\n\n".join(vision_notes)[:MAX_PDF_CHARS])
        if not chunks:
            return "(no text extracted from PDF)"
        out = "\n\n".join(chunks)
        if len(out) > MAX_PDF_CHARS * 2:
            out = out[: MAX_PDF_CHARS * 2] + "\n…(truncated)"
        return out
    finally:
        doc.close()


def transcribe_audio(att: Dict[str, Any]) -> str:
    """The words spoken in a voice clip, or an inline reason there are none."""
    from api import voice_stt

    filename = att.get("filename") or "voice clip"
    mime = str(att.get("mime") or "")
    if not mime.startswith("audio/"):
        mime = _AUDIO_MIME_BY_EXT.get(Path(filename).suffix.lower(), "audio/webm")
    try:
        text = voice_stt.transcribe(_read_bytes(att), mime=mime, max_bytes=MAX_AUDIO_BYTES)
    except (ValueError, RuntimeError) as e:
        # Same rule as images: say why, never a silent empty result.
        return f"(voice clip not transcribed — {e})"
    except Exception as e:
        return f"(voice clip not transcribed — {type(e).__name__})"
    return text[:MAX_TRANSCRIPT_CHARS] if text else "(no speech heard in this clip)"


def analyze_attachment(att: Dict[str, Any], status_cb: StatusCb = None) -> str:
    filename = att.get("filename") or "file"
    mime = _guess_mime(filename, att.get("mime"))

    if _is_audio(filename, mime):
        _emit(status_cb, f"Transcribing voice clip ({filename})...")
        return transcribe_audio(att)
    if _is_image(filename, mime):
        _emit(status_cb, f"Analyzing image ({filename})...")
        return describe_image(att)
    if _is_pdf(filename, mime):
        _emit(status_cb, f"Extracting PDF ({filename})...")
        return extract_pdf(att, status_cb=status_cb)
    return f"(unsupported file type: {mime or Path(filename).suffix or 'unknown'})"


def build_attachment_digest(
    attachments: Sequence[Dict[str, Any]],
    status_cb: StatusCb = None,
) -> str:
    """
    Plain-text analysis block for a set of attachments (no user message).

    Each attachment dict may include:
      filename, mime, path, url, and/or data (bytes).
    """
    if not attachments:
        return ""

    _emit(status_cb, "Analyzing attachments...")
    blocks: List[str] = []
    for att in attachments:
        if att.get("transcribed") is True:
            continue  # the user's message already carries this clip's words
        fname = att.get("filename") or "file"
        mime = _guess_mime(fname, att.get("mime"))
        try:
            body = analyze_attachment(att, status_cb=status_cb)
        except Exception as e:
            body = f"(analysis failed: {_short_error(e)})"
        if _is_audio(fname, mime):
            kind = "voice clip — transcript of what the user said"
        elif _is_pdf(fname, mime):
            kind = "PDF"
        elif _is_image(fname, mime):
            kind = "image"
        else:
            kind = "file"
        location = att.get("path") or (att.get("url") if _is_remote_url(att.get("url")) else "")
        head = f"• {fname} ({kind})" + (f" — {location}" if location else "")
        blocks.append(f"{head}:\n{body}")

    if not blocks:
        return ""
    return DIGEST_HEADER + "\n" + "\n\n".join(blocks)


def append_attachment_digest(
    message: str,
    attachments: Sequence[Dict[str, Any]],
    status_cb: StatusCb = None,
) -> str:
    """
    Append the digest after the user's text.

    Appending (rather than prefixing) is required: every slash-agent handler
    matches ``^/cursor``/``^/muse``/… against this string and slices the prompt
    off the front, so a leading digest would break command detection.
    """
    digest = build_attachment_digest(attachments, status_cb=status_cb)
    if not digest:
        return message
    user = (message or "").strip()
    return f"{user}\n\n{digest}" if user else digest


def augment_message_with_attachments(
    message: str,
    attachments: Sequence[Dict[str, Any]],
    status_cb: StatusCb = None,
) -> str:
    """Digest first, then the user message (Discord / non-slash callers)."""
    digest = build_attachment_digest(attachments, status_cb=status_cb)
    if not digest:
        return message
    user = (message or "").strip()
    return f"{digest}\n\nUser message: {user or '(see attached files)'}"


def resolve_upload_refs(
    refs: Sequence[Dict[str, Any]],
    uploads_root: Path,
) -> List[Dict[str, Any]]:
    """
    Validate client-supplied attachment refs; only allow paths under uploads_root.
    Returns sanitized attachment dicts with absolute paths.
    """
    root = uploads_root.resolve()
    out: List[Dict[str, Any]] = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        raw_path = ref.get("path") or ref.get("temp_path")
        if not raw_path:
            continue
        try:
            path = Path(raw_path).resolve()
        except Exception:
            continue
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        fname = ref.get("filename") or path.name
        mime = ref.get("mime") or _guess_mime(fname)
        item = {"filename": fname, "path": str(path), "mime": mime}
        # A voice clip whose words the composer already typed into the message.
        if ref.get("transcribed") is True and _is_audio(fname, mime):
            item["transcribed"] = True
        out.append(item)
    return out
