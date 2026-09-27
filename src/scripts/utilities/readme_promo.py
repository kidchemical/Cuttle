"""Landing-page style frames for README media.

Raw UI captures are placed inside a browser window over a soft gradient backdrop,
with the wordmark, an eyebrow, a headline, and a footer caption, then rendered
with Playwright. Stills embed the capture directly; animations render the
backdrop once and paste every frame into the window slot.
"""
from __future__ import annotations

import base64
import html
import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[3]
MASCOT = REPO / "src" / "img" / "cuttle-mascot_square.png"

INK = "#191b45"
MUTED = "#5d5f7e"
ACCENT = "#7048e8"

PALETTES = {
    "dawn": ("#fbf8f3", [("12% 8%", "#ffe6d6", "46%"), ("58% 0%", "#ece4ff", "52%"),
                          ("100% 100%", "#d4e9ff", "60%"), ("0% 100%", "#fff4e4", "40%")]),
    "lilac": ("#f7f3ff", [("0% 0%", "#e8ddff", "50%"), ("100% 10%", "#ffe0ef", "44%"),
                           ("80% 100%", "#dbe8ff", "58%"), ("10% 100%", "#f3ecff", "40%")]),
    "mint": ("#f3fbf8", [("0% 0%", "#dcf5ec", "48%"), ("100% 0%", "#e4e8ff", "50%"),
                          ("100% 100%", "#d6ecff", "56%"), ("0% 100%", "#fff2df", "40%")]),
    "peach": ("#fff7f1", [("0% 0%", "#ffe2d2", "48%"), ("100% 0%", "#efe3ff", "52%"),
                           ("90% 100%", "#ffeccc", "50%"), ("0% 100%", "#e3edff", "46%")]),
    "rose": ("#fff5f8", [("0% 0%", "#ffdcea", "48%"), ("100% 0%", "#e6e0ff", "52%"),
                          ("100% 100%", "#ffe7d6", "50%"), ("0% 100%", "#f0e6ff", "46%")]),
    "sky": ("#f2f7ff", [("0% 0%", "#dcebff", "50%"), ("100% 0%", "#e9e0ff", "50%"),
                         ("100% 100%", "#d6f4ee", "52%"), ("0% 100%", "#f6eeff", "40%")]),
}

FOOTER = "Open source &nbsp;·&nbsp; MIT &nbsp;·&nbsp; Windows + Linux"


@dataclass
class Frame:
    """One composition. ``layout`` is ``stage`` (headline over window) or ``hero``."""

    headline: str
    sub: str = ""
    eyebrow: str = ""
    label: str = ""
    palette: str = "dawn"
    layout: str = "stage"
    url: str = "Cuttle &nbsp;·&nbsp; 127.0.0.1:8080"
    command: str = ""
    dots: tuple[int, int] = (0, 0)
    phone: Image.Image | None = field(default=None, repr=False)


CANVAS = {"stage": (1600, 1080), "hero": (1600, 1000)}


def _data_url(im: Image.Image | Path, fmt: str = "PNG") -> str:
    if isinstance(im, Path):
        return f"data:image/png;base64,{base64.b64encode(im.read_bytes()).decode()}"
    buf = io.BytesIO()
    im.save(buf, fmt)
    return f"data:image/{fmt.lower()};base64,{base64.b64encode(buf.getvalue()).decode()}"


def _backdrop(palette: str) -> str:
    base, blobs = PALETTES[palette]
    layers = [f"radial-gradient(ellipse at {pos}, {color} 0%, transparent {reach})"
              for pos, color, reach in blobs]
    return ", ".join(layers) + f", {base}"


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:__W__px;height:__H__px;overflow:hidden}
body{font-family:Inter,'Helvetica Neue',Arial,sans-serif;color:__INK__;background:__BG__;
  -webkit-font-smoothing:antialiased;position:relative}
.grid{position:absolute;inset:0;background-image:
  linear-gradient(rgba(25,27,69,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(25,27,69,.035) 1px,transparent 1px);
  background-size:48px 48px;-webkit-mask-image:radial-gradient(ellipse at 50% 30%,#000 10%,transparent 70%)}
.brand{position:absolute;display:flex;align-items:center;gap:12px;font-weight:700;font-size:26px;letter-spacing:-.02em}
.brand img{width:40px;height:40px}
.eyebrow{font-weight:700;font-size:15px;letter-spacing:.16em;text-transform:uppercase;color:__ACCENT__}
h1{font-weight:800;letter-spacing:-.045em;line-height:1.02;color:__INK__}
h1 em{font-style:normal;background:linear-gradient(95deg,#7048e8 10%,#3b82f6 90%);-webkit-background-clip:text;
  background-clip:text;color:transparent}
.sub{color:__MUTED__;font-weight:400;letter-spacing:-.01em}
.footer{position:absolute;bottom:34px;font-size:15px;color:__MUTED__;font-weight:500}
.label{position:absolute;bottom:34px;right:56px;font-size:14px;font-weight:700;letter-spacing:.14em;color:__ACCENT__}
.window{position:absolute;background:#fff;border-radius:18px;overflow:hidden;
  box-shadow:0 0 0 1px rgba(25,27,69,.07),0 2px 4px rgba(25,27,69,.04),0 24px 48px -12px rgba(52,36,120,.22),0 60px 120px -30px rgba(52,36,120,.28)}
.chrome{height:40px;display:flex;align-items:center;padding:0 16px;background:#fbfbfd;border-bottom:1px solid rgba(25,27,69,.07);position:relative}
.lights{display:flex;gap:8px}.lights i{width:12px;height:12px;border-radius:50%;display:block}
.url{position:absolute;left:50%;transform:translateX(-50%);height:24px;min-width:300px;padding:0 18px;border-radius:7px;
  background:#eff0f5;color:#6b6d88;font-size:12.5px;font-weight:500;display:flex;align-items:center;justify-content:center}
.shot{display:block;width:100%;height:auto}
.slot{width:100%;background:#fff}
.cmd{display:inline-flex;align-items:center;gap:12px;height:56px;padding:0 24px 0 20px;border-radius:999px;background:#fff;
  box-shadow:0 0 0 1px rgba(25,27,69,.08),0 10px 30px -10px rgba(52,36,120,.3);font:500 17px 'JetBrains Mono',ui-monospace,monospace;color:__INK__}
.cmd b{color:__ACCENT__;font-weight:700}
.dots{display:flex;gap:8px}.dots i{width:26px;height:4px;border-radius:2px;background:rgba(25,27,69,.12);display:block}
.dots i.on{background:__ACCENT__;width:40px}
.phone{position:absolute;width:250px;border-radius:40px;background:#0f1024;padding:9px;
  box-shadow:0 0 0 1px rgba(255,255,255,.2) inset,0 40px 80px -20px rgba(30,20,80,.45),0 18px 36px -12px rgba(30,20,80,.3)}
.phone .screen{border-radius:31px;overflow:hidden;background:#fff;position:relative}
.phone .screen img{display:block;width:100%}
.phone .notch{position:absolute;top:8px;left:50%;transform:translateX(-50%);width:78px;height:22px;border-radius:12px;background:#0f1024;z-index:2}
"""


def _window(inner: str, url: str, style: str) -> str:
    return (f'<div class="window" style="{style}"><div class="chrome"><div class="lights">'
            '<i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i>'
            f'</div><div class="url">{url}</div></div>{inner}</div>')


def _brand(style: str) -> str:
    return f'<div class="brand" style="{style}"><img src="{_data_url(MASCOT)}" alt="">Cuttle</div>'


def _dots(active: int, total: int) -> str:
    if not total:
        return ""
    return '<div class="dots">' + "".join(
        f'<i class="{"on" if i == active else ""}"></i>' for i in range(total)) + "</div>"


def _stage(spec: Frame, inner: str, aspect: float) -> str:
    w, h = CANVAS["stage"]
    head_bottom, foot = 262, 70
    win_w = min(1180, (h - head_bottom - foot - 40) * aspect)
    win_h = 40 + win_w / aspect
    top = head_bottom + (h - head_bottom - foot - win_h) / 2
    head = f"""
<div style="position:absolute;left:0;right:0;top:78px;text-align:center">
  {f'<div class="eyebrow">{spec.eyebrow}</div>' if spec.eyebrow else ''}
  <h1 style="font-size:60px;margin-top:16px">{spec.headline}</h1>
  {f'<p class="sub" style="font-size:22px;margin-top:16px">{spec.sub}</p>' if spec.sub else ''}
</div>"""
    return (_brand("left:56px;top:40px") + head
            + _window(inner, spec.url, f"left:{(w - win_w) / 2}px;top:{top}px;width:{win_w}px")
            + f'<div class="footer" style="left:56px">{FOOTER}</div>'
            + (f'<div class="label">{spec.label}</div>' if spec.label else ""))


def _hero(spec: Frame, inner: str, aspect: float) -> str:
    w, h = CANVAS["hero"]
    win_w = 1060
    win_h = 40 + win_w / aspect
    top = (h - win_h) / 2 - 40
    left = 660
    phone = ""
    if spec.phone is not None:
        phone = (f'<div class="phone" style="left:{left - 30}px;bottom:36px">'
                 f'<div class="screen"><div class="notch"></div>'
                 f'<img src="{_data_url(spec.phone)}" alt=""></div></div>')
    text = f"""
<div style="position:absolute;left:80px;top:0;bottom:0;width:520px;display:flex;flex-direction:column;justify-content:center">
  {f'<div class="eyebrow">{spec.eyebrow}</div>' if spec.eyebrow else ''}
  <h1 style="font-size:78px;margin-top:22px">{spec.headline}</h1>
  {f'<p class="sub" style="font-size:24px;line-height:1.45;margin-top:26px;max-width:470px">{spec.sub}</p>' if spec.sub else ''}
  {f'<div style="margin-top:40px"><span class="cmd"><b>$</b>{html.escape(spec.command)}</span></div>' if spec.command else ''}
  {f'<div style="margin-top:52px">{_dots(*spec.dots)}</div>' if spec.dots[1] else ''}
</div>"""
    return (_brand("left:80px;top:52px") + text
            + _window(inner, spec.url, f"left:{left}px;top:{top}px;width:{win_w}px") + phone
            + f'<div class="footer" style="left:80px">{FOOTER}</div>'
            + (f'<div class="label">{spec.label}</div>' if spec.label else ""))


def _page(spec: Frame, inner: str, aspect: float) -> str:
    w, h = CANVAS[spec.layout]
    css = (CSS.replace("__W__", str(w)).replace("__H__", str(h)).replace("__BG__", _backdrop(spec.palette))
           .replace("__INK__", INK).replace("__MUTED__", MUTED).replace("__ACCENT__", ACCENT))
    body = (_hero if spec.layout == "hero" else _stage)(spec, inner, aspect)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        "<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800"
        "&family=JetBrains+Mono:wght@500;700&display=block' rel='stylesheet'>"
        f"<style>{css}</style></head><body><div class='grid'></div>{body}</body></html>"
    )


class Composer:
    """Renders frames with one shared browser."""

    def __init__(self, browser, scale: float = 1.25):
        self.browser = browser
        self.scale = scale

    def _render(self, spec: Frame, inner: str, aspect: float, scale: float):
        w, h = CANVAS[spec.layout]
        ctx = self.browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=scale)
        page = ctx.new_page()
        page.set_content(_page(spec, inner, aspect), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(150)
        return ctx, page

    def still(self, spec: Frame, shot: Image.Image) -> Image.Image:
        inner = f'<img class="shot" src="{_data_url(shot)}" alt="">'
        ctx, page = self._render(spec, inner, shot.width / shot.height, self.scale)
        out = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        ctx.close()
        return out

    def backdrop(self, spec: Frame, aspect: float, scale: float = 1.0) -> "Backdrop":
        ctx, page = self._render(spec, '<div class="slot" id="slot" style="aspect-ratio:%s"></div>' % aspect,
                                 aspect, scale)
        box = page.evaluate("""() => { const r = document.getElementById('slot').getBoundingClientRect();
                                        return [r.x, r.y, r.width, r.height]; }""")
        im = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
        ctx.close()
        x, y, bw, bh = (round(v * scale) for v in box)
        return Backdrop(im, (x, y, bw, bh), round(18 * scale))


@dataclass
class Backdrop:
    image: Image.Image
    slot: tuple[int, int, int, int]
    radius: int

    def __post_init__(self) -> None:
        _, _, w, h = self.slot
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, -self.radius, w - 1, h - 1), self.radius, fill=255)
        self._mask = mask

    def compose(self, frame: Image.Image) -> Image.Image:
        x, y, w, h = self.slot
        out = self.image.copy()
        out.paste(frame.convert("RGB").resize((w, h), Image.LANCZOS), (x, y), self._mask)
        return out
