"""Record and assemble the ~60s Cuttle product-launch promo.

    .venv/bin/python src/scripts/utilities/promo_video.py [--skip-record] [--keep]

Uses the same Cuttle Demo account and demo chats as the README captures (no real user data).
UI is driven headed on DISPLAY and recorded with OpenScreen when a window
match is found; Playwright's own webm is always kept as a fallback plate.
Titles, logo sweep, and music are cut with ffmpeg (DaVinci Resolve is not
installed and its Linux .run needs sudo + ~3 GB).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_gifs as gifs  # noqa: E402
import readme_screenshots as rs  # noqa: E402

REPO = rs.REPO
OUT_DIR = REPO / "docs" / "media" / "promo"
RAW = REPO / "temp" / "promo"
FFMPEG = Path(imageio_ffmpeg.get_ffmpeg_exe())
W, H = 1920, 1080
FPS = 30
DURATION = 60.0

gifs.VIEWPORT = {"width": W, "height": H}

TITLE = "Cuttle Promo Capture"
HERO_PROMPT = rs.HERO_USER.replace("/cursor ", "")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd[:12]), "..." if len(cmd) > 12 else "")
    kw.pop("stdout", None)
    kw.pop("stderr", None)
    proc = subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kw)
    if proc.returncode != 0:
        msg = (proc.stderr or b"").decode("utf-8", "replace")[-2000:]
        print(msg)
        raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=msg)
    return proc


def write_music(path: Path, seconds: float = 64.0, sr: int = 44100) -> None:
    """Minimal electronic bed: pad, pulse, arpeggio, kick that builds after 0:35."""
    n = int(seconds * sr)
    bpm = 96
    beat = 60.0 / bpm

    def env(i, a, d):
        t = i / sr
        if t < a:
            return t / a
        return math.exp(-(t - a) / d)

    samples = []
    for i in range(n):
        t = i / sr
        # Slow pad (fifth)
        pad = 0.11 * math.sin(2 * math.pi * 110 * t)
        pad += 0.07 * math.sin(2 * math.pi * 165 * t + 0.2)
        pad += 0.04 * math.sin(2 * math.pi * 220 * t)
        pad *= 0.5 + 0.5 * math.sin(2 * math.pi * t / 16)
        # Arp 16ths after the reveal
        step = int(t / (beat / 4)) % 8
        freqs = [330, 392, 494, 392, 330, 440, 494, 587]
        gate = 1.0 if (t % (beat / 4)) < (beat / 8) else 0.0
        arp = 0.0
        if t > 7.5:
            arp = 0.045 * math.sin(2 * math.pi * freqs[step] * t) * gate
            if t > 35:
                arp *= 1.6
        # Kick on downbeats after 0:08
        k = 0.0
        if t > 8:
            pos = t % beat
            k = 0.22 * math.sin(2 * math.pi * (90 * (1 - pos * 8)) * t) * math.exp(-pos * 14)
            if t > 47:
                k *= 1.35
        # Hat
        hat = 0.0
        if t > 18:
            pos = (t % (beat / 2))
            noise = ((i * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff - 0.5
            hat = 0.03 * noise * math.exp(-pos * 40)
        # Risers into payoff
        rise = 0.0
        if 44 < t < 50:
            rise = 0.04 * ((t - 44) / 6) * math.sin(2 * math.pi * (200 + 400 * (t - 44) / 6) * t)
        mix = (pad + arp + k + hat + rise) * (0.35 + 0.65 * min(1.0, t / 6))
        # Fade out last 3s
        if t > seconds - 3:
            mix *= max(0.0, (seconds - t) / 3)
        samples.append(max(-1.0, min(1.0, mix)))

    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(s * 30000)) for s in samples))
    print("wrote", path)


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render_logo_clip(path: Path, seconds: float, subtitle: str, fade_text_at: float = 2.4) -> None:
    logo = Image.open(REPO / "src" / "img" / "cuttle-logo.png").convert("RGBA")
    scale = 0.42 * W / logo.width
    logo = logo.resize((int(logo.width * scale), int(logo.height * scale)), Image.Resampling.LANCZOS)
    lx, ly = (W - logo.width) // 2, int(H * 0.28)
    fnt = font(64)
    small = font(28)
    n = int(seconds * FPS)
    raw = RAW / "logo_frames.rgb"
    raw.parent.mkdir(parents=True, exist_ok=True)
    with raw.open("wb") as out:
        for i in range(n):
            t = i / FPS
            img = Image.new("RGB", (W, H), (0, 0, 0))
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            layer.paste(logo, (lx, ly), logo)
            # Sweeping specular bar
            bar = Image.new("L", (W, H), 0)
            bx = int(-W * 0.4 + (t / max(seconds * 0.85, 0.1)) * W * 1.8)
            draw = ImageDraw.Draw(bar)
            draw.polygon([(bx, 0), (bx + 160, 0), (bx - 40, H), (bx - 200, H)], fill=255)
            bar = bar.filter(ImageFilter.GaussianBlur(80))
            hi = Image.new("RGBA", (W, H), (255, 255, 255, 0))
            hi.putalpha(bar.point(lambda v: int(v * 0.45)))
            composed = Image.alpha_composite(layer, Image.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 0)), hi))
            # Soft bloom
            glow = composed.filter(ImageFilter.GaussianBlur(18))
            glow = ImageEnhance.Brightness(glow).enhance(1.4)
            frame = Image.composite(glow.convert("RGB"), img, composed.split()[-1].point(lambda v: min(255, v * 2)))
            frame = Image.blend(frame, composed.convert("RGB"), 0.72)
            d = ImageDraw.Draw(frame)
            if t >= fade_text_at and subtitle:
                a = min(1.0, (t - fade_text_at) / 1.2)
                col = int(230 * a)
                tw = d.textlength(subtitle, font=fnt)
                d.text(((W - tw) / 2, ly + logo.height + 48), subtitle, font=fnt, fill=(col, col, col))
            # Tiny wordmark under
            if t > 1.0:
                a = min(1.0, (t - 1.0) / 0.8)
                col = int(140 * a)
                tag = "CUTTLE"
                tw = d.textlength(tag, font=small)
                d.text(((W - tw) / 2, ly - 36), tag, font=small, fill=(col, col, col))
            out.write(frame.tobytes())
    run([
        str(FFMPEG), "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-r", str(FPS), "-i", str(raw), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "16", str(path),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raw.unlink(missing_ok=True)
    print("wrote", path)


def title_png(path: Path, lines: list[str], size: int = 56, fill=(32, 32, 36, 235)) -> None:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fnt = font(size)
    y = int(H * 0.72)
    for line in lines:
        tw = d.textlength(line, font=fnt)
        d.text(((W - tw) / 2 + 1, y + 1), line, font=fnt, fill=(255, 255, 255, 200))
        d.text(((W - tw) / 2, y), line, font=fnt, fill=fill)
        y += size + 16
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def logo_card() -> Path:
    """Transparent logo, no wordmark — used only on the outro."""
    src = Image.open(REPO / "src" / "img" / "cuttle-logo.png").convert("RGBA")
    # The file already includes the word "cuttle"; scale the whole mark.
    scale = 0.38 * W / src.width
    src = src.resize((int(src.width * scale), int(src.height * scale)), Image.Resampling.LANCZOS)
    card = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card.paste(src, ((W - src.width) // 2, int(H * 0.28)), src)
    path = RAW / "logo-card.png"
    card.save(path)
    return path


X264 = [
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), "-crf", "17",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
]


def spaces_for(chats: dict) -> list:
    return [
        ("Build", gifs.leaf(chats["hero"])),
        ("Research", gifs.group("horizontal", [
            gifs.leaf(chats["cost"], flex="1.1 1 0%"),
            gifs.group("vertical", [
                gifs.leaf(chats["standup"]),
                gifs.leaf(page="/dashboards_page.html"),
            ], flex="0.9 1 0%"),
        ])),
        ("Studio", gifs.group("horizontal", [
            gifs.leaf(page="/settings_page.html", flex="0.85 1 0%"),
            gifs.leaf(chats["hero"], flex="1.15 1 0%"),
        ])),
        ("Ops", gifs.group("horizontal", [
            gifs.leaf(chats["render"]), gifs.leaf(chats["standup"]), gifs.leaf(chats["cost"]),
        ])),
        ("Focus", gifs.group("vertical", [
            gifs.leaf(chats["hero"], flex="1.2 1 0%"),
            gifs.leaf(chats["cost"], flex="0.8 1 0%"),
        ])),
    ]


BURSTS = [
    ("sakura", 0, "Research"),
    ("mint", 1, "Ops"),
    ("paper", 2, "Focus"),
    ("lavender", 3, "Studio"),
    ("synthwave", 4, "Ops"),
    ("nord", 5, "Research"),
    ("matrix", 0, "Focus"),
    ("graphite", 1, "Ops"),
    ("rgb", 2, "Research"),
    ("midnight", 3, "Studio"),
    ("light", 4, "Build"),
    ("sakura", 5, "Ops"),
]


def record_ui(browser, auth_state, base, chats) -> tuple[Path, Path]:
    """Return (main light-mode demo mp4, bursts mp4 of 0.5s setups)."""
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "pw").mkdir(exist_ok=True)
    burst_dir = RAW / "bursts"
    burst_dir.mkdir(exist_ok=True)

    seed = gifs.shell_seed(spaces_for(chats), {
        "theme": "light",
        "cuttleVideoBackgroundEnabled": "0",
        "cuttleVideoBackgroundList": json.dumps(gifs.DEMO_VIDEOS),
        "cuttleVideoBackgroundOpacity": "28",
        "cuttleVideoBackgroundBlendTarget": "black",
        "cuttleVideoBackgroundDuration": "600",
    })
    page = open_shell(browser, auth_state, base, seed)
    for name in ("Research", "Studio", "Ops", "Focus", "Build"):
        page.locator(".shell-space-tab", has_text=name).click()
        page.wait_for_timeout(900)
    page.locator(".shell-space-tab", has_text="Build").click()
    page.wait_for_timeout(600)
    for frame in page.frames:
        try:
            frame.evaluate(rs.SCROLL_CHAT_TOP)
        except Exception:
            pass

    demo = gifs.Demo(page)
    demo.caption(None)

    cf = chat_frame(page)
    cf.evaluate("""() => {
        document.querySelectorAll('.message.assistant').forEach((e) => { e.style.opacity = '0'; });
        const inp = document.getElementById('chatInput');
        if (inp) { inp.value = ''; inp.dispatchEvent(new Event('input', {bubbles:true})); }
    }""")
    demo.wait(0.5)
    box = cf.locator("#chatInput").bounding_box()
    if box:
        demo.click(box["x"] + 80, box["y"] + box["height"] / 2)
    cf.locator("#chatInput").type(HERO_PROMPT, delay=16)
    demo.wait(0.35)
    send = cf.locator("#sendButton").bounding_box()
    if send:
        demo.click(send["x"] + send["width"] / 2, send["y"] + send["height"] / 2)
    demo.wait(0.4)
    cf.evaluate("""() => {
        const inp = document.getElementById('chatInput');
        if (inp) inp.value = '';
        document.querySelectorAll('.message.assistant').forEach((e) => {
            e.style.transition = 'opacity 1.0s ease';
            e.style.opacity = '1';
        });
    }""")
    try:
        cf.evaluate(rs.SCROLL_CHAT_TOP)
    except Exception:
        pass
    demo.wait(5.0)

    logo = demo.rects(".rail-logo-clickable")[0]
    demo.click(logo["cx"], logo["cy"])
    demo.wait(1.15)
    logo = demo.rects(".rail-logo-clickable")[-1]
    demo.click(logo["cx"], logo["cy"], modifier="Alt", label="Alt")
    demo.wait(1.0)
    handles = demo.rects(".split-resize-handle")
    if handles:
        v = max(handles, key=lambda h: h["h"])
        demo.drag(v["cx"], v["cy"], v["cx"] - 150, v["cy"])
        demo.wait(0.3)
    tab = next(t for t in demo.rects(".shell-space-tab") if t["text"].startswith("Research"))
    demo.click(tab["cx"], tab["cy"])
    demo.wait(2.2)
    handles = demo.rects(".split-resize-handle")
    if handles:
        hd = max(handles, key=lambda r: r["h"])
        demo.drag(hd["cx"], hd["cy"], hd["cx"] + 130, hd["cy"])
        demo.wait(0.8)
    demo.wait(0.6)

    pw_video = Path(page.video.path()) if page.video else None
    page.evaluate("""() => {
        const c = document.getElementById('demo-cursor');
        if (c) c.style.display = 'none';
        const k = document.getElementById('demo-keys');
        if (k) k.style.display = 'none';
    }""")

    # Bursts: theme + wallpaper + layout, captured as stills held 0.5s in the cut.
    page.evaluate("""() => {
        try { localStorage.setItem('cuttleVideoBackgroundEnabled', '1'); } catch (e) {}
        if (window.CuttleVideoBackground && window.CuttleVideoBackground.setEnabled)
            window.CuttleVideoBackground.setEnabled(true);
    }""")
    for i, (theme, vid_i, space) in enumerate(BURSTS):
        url = gifs.DEMO_VIDEOS[vid_i % len(gifs.DEMO_VIDEOS)]
        page.evaluate(
            """([theme, url]) => {
                localStorage.setItem('theme', theme);
                if (window.CuttleUiBoot) window.CuttleUiBoot.applyTheme(theme);
                document.querySelectorAll('.shell-main iframe').forEach((f) => {
                    try {
                        f.contentWindow.localStorage.setItem('theme', theme);
                        if (f.contentWindow.CuttleUiBoot) f.contentWindow.CuttleUiBoot.applyTheme(theme);
                    } catch (e) {}
                });
                if (window.CuttleVideoBackground && window.CuttleVideoBackground.playNow)
                    window.CuttleVideoBackground.playNow(url);
            }""",
            [theme, url],
        )
        try:
            page.locator(".shell-space-tab", has_text=space).click(timeout=2000)
        except Exception:
            pass
        page.wait_for_timeout(3800)
        for frame in page.frames:
            try:
                frame.evaluate(rs.SCROLL_CHAT_TOP)
            except Exception:
                pass
        page.screenshot(path=str(burst_dir / f"{i:02d}.png"), type="png")
        print("burst", i, theme, space)

    page.context.close()

    dest = RAW / "playwright-ui.mp4"
    if not pw_video or not pw_video.exists():
        raise RuntimeError("no UI recording produced")
    run([str(FFMPEG), "-y", "-i", str(pw_video), *X264, str(dest)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    burst_list = RAW / "bursts.txt"
    clips = []
    for i in range(len(BURSTS)):
        png = burst_dir / f"{i:02d}.png"
        mp4 = burst_dir / f"{i:02d}.mp4"
        run([
            str(FFMPEG), "-y", "-loop", "1", "-t", "0.5", "-i", str(png),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:white,fps={FPS}",
            *X264, "-an", str(mp4),
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        clips.append(mp4)
    burst_list.write_text("".join(f"file '{p}'\n" for p in clips))
    bursts = RAW / "bursts.mp4"
    run([
        str(FFMPEG), "-y", "-f", "concat", "-safe", "0", "-i", str(burst_list),
        *X264, "-an", str(bursts),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dest, bursts


def wait_sculpture(frames: int = 600, timeout: int = 240) -> Path:
    folder = RAW / "sculpture"
    t0 = time.time()
    while time.time() - t0 < timeout:
        n = len(list(folder.glob("f*.png")))
        if n >= frames:
            return folder
        time.sleep(2)
    have = len(list(folder.glob("f*.png")))
    if have < 90:
        raise RuntimeError(f"sculpture render incomplete ({have} frames)")
    print("sculpture partial", have)
    return folder


def frames_to_mp4(folder: Path, start: int, end: int, dest: Path) -> None:
    # Blender saves f0001.png — use a trimmed copy list if range isn't from 1.
    run([
        str(FFMPEG), "-y",
        "-start_number", str(start),
        "-i", str(folder / "f%04d.png"),
        "-frames:v", str(end - start + 1),
        "-vf", f"scale={W}:{H},fps={FPS},fade=t=in:st=0:d=1.15:color=white",
        *X264, "-an", str(dest),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def assemble(ui: Path, bursts: Path | None = None) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    music = RAW / "bed.wav"
    write_music(music)
    concept = RAW / "title-concept.png"
    intent = RAW / "title-intent.png"
    meet = RAW / "title-meet.png"
    tag = RAW / "title-tag.png"
    title_png(concept, ["Your AI. Your machines.", "Your workflow."], 52)
    title_png(intent, ["From intention to execution."], 50)
    title_png(meet, ["Meet Cuttle."], 70)
    title_png(tag, ["Build beyond the prompt."], 48)
    logo = logo_card()

    folder = wait_sculpture()
    n = max(int(p.stem[1:]) for p in folder.glob("f*.png"))
    intro_end = min(240, n)
    outro_start = min(intro_end + 1, n)
    intro_raw = RAW / "intro-sc.mp4"
    outro_raw = RAW / "outro-sc.mp4"
    frames_to_mp4(folder, 1, intro_end, intro_raw)
    # Outro: remaining frames, fade in from white already applied in frames_to_mp4
    frames_to_mp4(folder, outro_start, n, outro_raw)

    intro = RAW / "intro.mp4"
    run([
        str(FFMPEG), "-y", "-i", str(intro_raw), "-loop", "1", "-t", "8", "-i", str(meet),
        "-filter_complex",
        "[1:v]format=rgba,fade=t=in:st=1.3:d=1.2:alpha=1,fade=t=out:st=6.4:d=0.7:alpha=1[t];"
        "[0:v][t]overlay=0:0,fade=t=out:st=7.05:d=0.9:color=white[v]",
        "-map", "[v]", *X264, "-an", "-t", "8", str(intro),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    outro = RAW / "outro.mp4"
    run([
        str(FFMPEG), "-y",
        "-i", str(outro_raw),
        "-loop", "1", "-i", str(logo),
        "-loop", "1", "-i", str(tag),
        "-filter_complex",
        "[0:v]trim=duration=12,setpts=PTS-STARTPTS[base];"
        "[1:v]format=rgba,fade=t=in:st=0.35:d=1.7:alpha=1,fade=t=out:st=9.3:d=2.0:alpha=1[lg];"
        "[2:v]format=rgba,fade=t=in:st=2.2:d=1.2:alpha=1,fade=t=out:st=9.2:d=1.8:alpha=1[tg];"
        "[base][lg]overlay=0:0[b1];[b1][tg]overlay=0:0,fade=t=out:st=9.6:d=2.3:color=white[v]",
        "-map", "[v]", *X264, "-an", "-t", "12", str(outro),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Main demo: skip Playwright's pre-roll (space warmup) — keep the acting.
    ui_cut = RAW / "ui-cut.mp4"
    run([
        str(FFMPEG), "-y", "-ss", "9.2", "-i", str(ui), "-t", "28",
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:white,fps={FPS},"
               f"fade=t=in:st=0:d=0.85:color=white,fade=t=out:st=27.2:d=0.75:color=white",
        *X264, "-an", str(ui_cut),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ui_titled = RAW / "ui-titled.mp4"
    run([
        str(FFMPEG), "-y",
        "-i", str(ui_cut),
        "-loop", "1", "-t", "28", "-i", str(concept),
        "-loop", "1", "-t", "28", "-i", str(intent),
        "-filter_complex",
        "[0:v]format=yuv420p[base];"
        "[1:v]format=rgba,fade=t=in:st=0.4:d=0.7:alpha=1,fade=t=out:st=8.2:d=0.6:alpha=1[c];"
        "[2:v]format=rgba,fade=t=in:st=9.0:d=0.6:alpha=1,fade=t=out:st=16.5:d=0.6:alpha=1[i];"
        "[base][c]overlay=0:0[b1];[b1][i]overlay=0:0[v]",
        "-map", "[v]", "-t", "28", *X264, str(ui_titled),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    burst_f = RAW / "bursts-f.mp4"
    burst_in = bursts if bursts and bursts.exists() else RAW / "bursts.mp4"
    run([
        str(FFMPEG), "-y", "-i", str(burst_in),
        "-vf", f"scale={W}:{H},fps={FPS},fade=t=in:st=0:d=0.15:color=white,fade=t=out:st=5.55:d=0.4:color=white",
        *X264, "-an", str(burst_f),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    pieces = [intro, ui_titled, burst_f, outro]
    concat_list = RAW / "concat.txt"
    concat_list.write_text("".join(f"file '{p}'\n" for p in pieces))
    silent = RAW / "silent60.mp4"
    run([
        str(FFMPEG), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        *X264, "-an", str(silent),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = OUT_DIR / "cuttle-launch-60s.mp4"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run([
        str(FFMPEG), "-y", "-i", str(silent), "-i", str(music),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        "-movflags", "+faststart", str(out),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("FINAL", out, out.stat().st_size)
    return out


def open_shell(browser, auth_state, base, seed):
    ctx = browser.new_context(
        ignore_https_errors=True,
        viewport=gifs.VIEWPORT,
        storage_state=auth_state,
        record_video_dir=str(RAW / "pw"),
        record_video_size={"width": W, "height": H},
        device_scale_factor=1,
    )
    ctx.route("**/api/**", lambda route: route.fulfill(json={"success": True})
              if route.request.method != "GET" else route.continue_())
    demo_wallpaper = {"success": True, "video_background": {
        "playlists": {"default": gifs.DEMO_VIDEOS}, "active_playlist": "default",
        "urls": gifs.DEMO_VIDEOS, "duration": 600, "opacity": 40, "enabled": True}}
    ctx.route("**/api/settings/video-background",
              lambda route: route.fulfill(json=demo_wallpaper)
              if route.request.method == "GET" else route.fulfill(json={"success": True}))
    ctx.add_init_script(gifs.OVERLAY_JS.replace("__SEED__", json.dumps(seed)))
    ctx.add_init_script(f"setInterval(() => {{ try {{ document.title = {TITLE!r}; }} catch (e) {{}} }}, 500);")
    page = ctx.new_page()
    page.goto(f"{base}/", wait_until="networkidle")
    page.wait_for_timeout(2800)
    return page


def chat_frame(page):
    return next(f for f in page.frames if "chat_page" in f.url and "settings" not in f.url)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://127.0.0.1:8080")
    ap.add_argument("--skip-record", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DISPLAY", ":1")

    alt = RAW / "playwright-ui.mp4"
    bursts = RAW / "bursts.mp4"
    if args.skip_record:
        if not alt.exists():
            raise SystemExit("no recording to assemble")
        plate, burst_path = alt, bursts if bursts.exists() else None
    else:
        db = rs.get_auth_db()
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=not os.environ.get("CUTTLE_PROMO_HEADED"),
                args=["--autoplay-policy=no-user-gesture-required", f"--window-size={W},{H}"],
            )
            ctx = browser.new_context(ignore_https_errors=True, viewport=gifs.VIEWPORT)
            with rs.fixture_browser_auth(ctx, args.base, db=db) as user_id:
                auth_state = ctx.storage_state()
                ctx.close()
                chats = {}
                try:
                    chats = rs.seed_demo_chats(db, user_id)
                    plate, burst_path = record_ui(browser, auth_state, args.base, chats)
                finally:
                    browser.close()
                    if not args.keep:
                        for sid in chats.values():
                            db.delete_chat_session(sid, user_id)
    assemble(plate, burst_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
