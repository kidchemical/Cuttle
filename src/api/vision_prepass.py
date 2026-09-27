"""
Vision / document pre-pass for chat attachments.

Runs before the agent (pipeline or CLI harness): describes images (Claude
vision → OpenAI vision → OCR) and extracts PDF text (rasterize + vision when
pages are image-heavy). Produces a plain-text digest the rest of the stack can
use, since none of the CLI harnesses (Cursor, Codex, Muse, Hermes) accept image
input.
"""

from __future__ import annotations

import base64
import io
import mimetypes
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

StatusCb = Optional[Callable[[str], None]]

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
PDF_EXTS = {".pdf"}
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
    text = " ".join(str(exc).split())
    return text[:240] or exc.__class__.__name__


def _describe_image_claude(
    *,
    data: Optional[bytes] = None,
    url: Optional[str] = None,
    mime: str = "image/png",
    filename: str = "image",
) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    from anthropic import Anthropic

    if data is None and _is_remote_url(url):
        source: Dict[str, Any] = {"type": "url", "url": url}
    else:
        if not data:
            raise ValueError("image data required")
        data, mime = _normalize_image_bytes(data, mime)
        source = {
            "type": "base64",
            "media_type": mime,
            "data": base64.standard_b64encode(data).decode("ascii"),
        }

    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=VISION_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": source},
                    {"type": "text", "text": VISION_PROMPT.format(filename=filename)},
                ],
            }
        ],
    )
    parts = []
    for block in resp.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _describe_image_openai(
    *,
    data: Optional[bytes] = None,
    url: Optional[str] = None,
    mime: str = "image/png",
    filename: str = "image",
) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    from openai import OpenAI

    if data is None and _is_remote_url(url):
        image_url = url
    else:
        if not data:
            raise ValueError("image data required")
        data, mime = _normalize_image_bytes(data, mime)
        b64 = base64.standard_b64encode(data).decode("ascii")
        image_url = f"data:{mime};base64,{b64}"

    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=OPENAI_VISION_MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT.format(filename=filename)},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
    )
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return ""
    return (choices[0].message.content or "").strip()


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
    Try each vision provider, then OCR.

    A provider that is out of credit / missing a key must not degrade into a
    silent "(no text found)" — the agent then confidently tells the user no
    image arrived. Failures are reported inline instead.
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
    if data is None:
        return f"(image not analyzed — {detail})"

    ocr = _ocr_image_fallback(data, filename)
    if ocr.startswith("[OCR] ") and "(no text found)" not in ocr:
        return f"[vision unavailable, OCR text only — {detail}]\n{ocr[len('[OCR] '):]}"
    return f"(image not analyzed — {detail}; OCR found no text)"


def _ocr_image_fallback(data: bytes, filename: str) -> str:
    try:
        from tools.ocr.ocr_manager import OCRManager

        ocr = OCRManager()
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".png", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            result = ocr.extract_text_from_image(tmp_path)
            text = (result or {}).get("text") if isinstance(result, dict) else str(result or "")
            text = (text or "").strip()
            return f"[OCR] {text}" if text else "[OCR] (no text found)"
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as e:
        return f"(vision/OCR unavailable: {e})"


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


def analyze_attachment(att: Dict[str, Any], status_cb: StatusCb = None) -> str:
    filename = att.get("filename") or "file"
    mime = _guess_mime(filename, att.get("mime"))

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
        fname = att.get("filename") or "file"
        mime = _guess_mime(fname, att.get("mime"))
        try:
            body = analyze_attachment(att, status_cb=status_cb)
        except Exception as e:
            body = f"(analysis failed: {_short_error(e)})"
        if _is_pdf(fname, mime):
            kind = "PDF"
        elif _is_image(fname, mime):
            kind = "image"
        else:
            kind = "file"
        location = att.get("path") or (att.get("url") if _is_remote_url(att.get("url")) else "")
        head = f"• {fname} ({kind})" + (f" — {location}" if location else "")
        blocks.append(f"{head}:\n{body}")

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
        out.append({"filename": fname, "path": str(path), "mime": mime})
    return out
