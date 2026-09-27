"""Generate Android/iOS app icons from src/img/cuttle-logo.png (mascot only)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]  # Cuttle repo root
SRC = ROOT / "src" / "img" / "cuttle-logo.png"
ANDROID_RES = ROOT / "apps" / "mobile" / "android" / "app" / "src" / "main" / "res"
IOS_ICONSET = (
    ROOT
    / "apps"
    / "mobile"
    / "ios"
    / "App"
    / "App"
    / "Assets.xcassets"
    / "AppIcon.appiconset"
)
ASSETS_OUT = ROOT / "apps" / "mobile" / "assets" / "icon"

BG = (11, 15, 20, 255)  # #0b0f14 — matches mobile shell


def extract_mascot(logo: Image.Image) -> Image.Image:
    """Crop the cuttlefish from the left of the wide logo (ignore wordmark)."""
    rgba = logo.convert("RGBA")
    # Hard-limit to left portion so "cuttle" wordmark never enters the icon.
    cut_w = min(int(rgba.width * 0.46), 480)
    left = rgba.crop((0, 0, cut_w, rgba.height))
    pixels = left.load()
    w, h = left.size
    min_x, min_y, max_x, max_y = w, h, 0, 0
    found = False
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a < 16:
                continue
            # Prefer purple/lavender body (+ eye highlight); skip near-black outlines alone
            brightness = r + g + b
            if brightness < 80:
                continue
            is_purple = b >= 80 and r >= 60 and b >= g
            is_highlight = r > 200 and g > 200 and b > 200
            if not (is_purple or is_highlight):
                continue
            found = True
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if not found:
        return left
    # Expand slightly so dark outlines around the body are included
    pad = 24
    box = (
        max(0, min_x - pad),
        max(0, min_y - pad),
        min(w, max_x + 1 + pad),
        min(h, max_y + 1 + pad),
    )
    return left.crop(box)


def fit_on_canvas(mascot: Image.Image, size: int, scale: float = 0.72) -> Image.Image:
    """Place mascot centered on a square canvas with brand background."""
    canvas = Image.new("RGBA", (size, size), BG)
    target = int(size * scale)
    m = mascot.copy()
    m.thumbnail((target, target), Image.Resampling.LANCZOS)
    x = (size - m.width) // 2
    y = (size - m.height) // 2
    canvas.alpha_composite(m, (x, y))
    return canvas


def fit_foreground(mascot: Image.Image, size: int, scale: float = 0.66) -> Image.Image:
    """Adaptive-icon foreground: transparent canvas, mascot in safe zone."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    target = int(size * scale)
    m = mascot.copy()
    m.thumbnail((target, target), Image.Resampling.LANCZOS)
    x = (size - m.width) // 2
    y = (size - m.height) // 2
    canvas.alpha_composite(m, (x, y))
    return canvas


def fit_chat_avatar(mascot: Image.Image, size: int = 512, zoom: float = 1.12) -> Image.Image:
    """Chat profile avatar: cover-crop so the mascot fills the circle."""
    mw, mh = mascot.size
    ratio = max(size / mw, size / mh) * zoom
    nw, nh = max(1, int(mw * ratio)), max(1, int(mh * ratio))
    m = mascot.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - size) // 2
    top = (nh - size) // 2
    cropped = m.crop((left, top, left + size, top + size))
    # Composite onto brand bg in case of any transparency at edges
    canvas = Image.new("RGBA", (size, size), BG)
    canvas.alpha_composite(cropped)
    return canvas


def main() -> None:
    logo = Image.open(SRC)
    mascot = extract_mascot(logo)
    ASSETS_OUT.mkdir(parents=True, exist_ok=True)
    mascot.save(ASSETS_OUT / "mascot.png")

    # Master icons
    icon_1024 = fit_on_canvas(mascot, 1024, scale=0.78)
    icon_1024.save(ASSETS_OUT / "icon-1024.png")
    fg_432 = fit_foreground(mascot, 432, scale=0.68)
    fg_432.save(ASSETS_OUT / "foreground-432.png")

    # Android legacy + round + foreground per density
    densities = {
        "mipmap-mdpi": 48,
        "mipmap-hdpi": 72,
        "mipmap-xhdpi": 96,
        "mipmap-xxhdpi": 144,
        "mipmap-xxxhdpi": 192,
    }
    fg_densities = {
        "mipmap-mdpi": 108,
        "mipmap-hdpi": 162,
        "mipmap-xhdpi": 216,
        "mipmap-xxhdpi": 324,
        "mipmap-xxxhdpi": 432,
    }

    for folder, size in densities.items():
        out_dir = ANDROID_RES / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        icon = fit_on_canvas(mascot, size, scale=0.78)
        icon.save(out_dir / "ic_launcher.png")
        icon.save(out_dir / "ic_launcher_round.png")

    for folder, size in fg_densities.items():
        out_dir = ANDROID_RES / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        fit_foreground(mascot, size, scale=0.68).save(out_dir / "ic_launcher_foreground.png")

    # iOS universal 1024
    IOS_ICONSET.mkdir(parents=True, exist_ok=True)
    icon_1024.save(IOS_ICONSET / "AppIcon-512@2x.png")

    # Chat avatar — cover-cropped so it reads large in the message circle
    chat = fit_chat_avatar(mascot, 512, zoom=1.12)
    chat_out = ROOT / "src" / "img" / "cuttle-avatar.png"
    chat.save(chat_out)
    chat.save(ASSETS_OUT / "chat-avatar.png")
    print(f"Wrote chat avatar: {chat_out}")

    print(f"Mascot crop: {mascot.size}")
    print(f"Wrote Android mipmaps under {ANDROID_RES}")
    print(f"Wrote iOS AppIcon: {IOS_ICONSET / 'AppIcon-512@2x.png'}")
    print(f"Masters in {ASSETS_OUT}")


if __name__ == "__main__":
    main()
