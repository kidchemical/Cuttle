"""Sample screen pixels and map to Govee RGB (ambient / bias lighting)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from PIL import Image

from tools.govee.govee_api import GoveeError, govee_set_color


def _average_rgb_from_image(img: Image.Image, downscale: int = 48) -> Tuple[int, int, int]:
    w, h = img.size
    if w < 1 or h < 1:
        raise GoveeError("Empty capture region")
    small = max(8, min(downscale, 128))
    im = img.convert("RGB").resize((small, small), Image.Resampling.LANCZOS)
    pixels = list(im.getdata())
    n = len(pixels)
    if n == 0:
        raise GoveeError("No pixels sampled")
    r = sum(p[0] for p in pixels) // n
    g = sum(p[1] for p in pixels) // n
    b = sum(p[2] for p in pixels) // n
    return r, g, b


def sample_screen_average_rgb(
    monitor: int = 1,
    left: Optional[int] = None,
    top: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    downscale: int = 48,
) -> Tuple[int, int, int]:
    """Capture monitor or region and return mean RGB (mss or PIL ImageGrab)."""
    img = None
    try:
        import mss
        from PIL import Image as _Image

        with mss.mss() as sct:
            monitors = sct.monitors
            if monitor < 1 or monitor >= len(monitors):
                raise GoveeError(f"Invalid monitor index {monitor}")
            mon = monitors[monitor]
            region = {
                "left": int(left if left is not None else mon["left"]),
                "top": int(top if top is not None else mon["top"]),
                "width": int(width if width is not None else mon["width"]),
                "height": int(height if height is not None else mon["height"]),
            }
            raw = sct.grab(region)
            img = _Image.frombytes("RGB", raw.size, raw.rgb)
    except Exception:
        from PIL import ImageGrab

        bbox = None
        if None not in (left, top, width, height):
            bbox = (int(left), int(top), int(left) + int(width), int(top) + int(height))
        img = ImageGrab.grab(bbox=bbox)
    if img is None:
        raise GoveeError("Screen capture failed")
    return _average_rgb_from_image(img, downscale=downscale)


def govee_sync_color_from_screen(
    device_id: str,
    model: str,
    monitor: int = 1,
    left: Optional[int] = None,
    top: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    downscale: int = 48,
    min_brightness: int = 8,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sample screen (full monitor or rectangle) and set Govee device to the average RGB.
    Boosts very dark colors slightly so lights stay visible (bias lighting).
    """
    r, g, b = sample_screen_average_rgb(
        monitor=monitor, left=left, top=top, width=width, height=height, downscale=downscale
    )
    # Lift near-black so strips show something at night
    m = max(r, g, b)
    if m < min_brightness and m > 0:
        scale = min_brightness / m
        r = min(255, int(r * scale))
        g = min(255, int(g * scale))
        b = min(255, int(b * scale))
    msg = govee_set_color(device_id, model, r, g, b, api_key=api_key)
    return {"r": r, "g": g, "b": b, "message": msg}
