"""Record README demo animations (animated WebP) of the running web UI into docs/media/.

    .venv/bin/python src/scripts/utilities/readme_gifs.py [customize] [multiplex] [--keep]

Signs in as a throwaway guest, seeds demo chats, drives the app shell with a
drawn cursor, and records frames from the Chrome DevTools screencast. Every
non-GET API call from the page is stubbed, so the demo cannot change real
settings, wallpapers, or the pane map agents read. Frames are pasted into the
same landing-page frame as the stills (readme_promo.py).
"""
from __future__ import annotations

import argparse
import base64
import bisect
import io
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import readme_screenshots as rs  # noqa: E402

promo = rs.promo

OUT = rs.OUT
VIEWPORT = {"width": 1280, "height": 800}
OUT_WIDTH = 1280
FPS = 12

FRAMES = {
    "multiplex": promo.Frame(
        palette="peach", eyebrow="Workspace", label="WORKSPACE &nbsp;/&nbsp; 03",
        headline="Split, resize, <em>switch spaces.</em>",
        sub="Tile chats and dashboards any way you like. Every space keeps its own layout."),
    "customize": promo.Frame(
        palette="rose", eyebrow="Open source &nbsp;·&nbsp; MIT", label="MAKE IT YOURS &nbsp;/&nbsp; 04",
        headline="Make it yours, <em>one prompt at a time.</em>",
        sub="Ask any agent to reshape Cuttle for your workflow. Themes and wallpapers are just the start."),
}

DEMO_VIDEOS = [
    "https://www.youtube.com/watch?v=hOgVAYpHPCc",
    "https://www.youtube.com/watch?v=0WQTZvunC2Q",
    "https://www.youtube.com/watch?v=B30S0Vr9N9A",
    "https://www.youtube.com/watch?v=KM9ptQ2Tz3s",
    "https://www.youtube.com/watch?v=Lj497ZMwhhc",
    "https://www.youtube.com/watch?v=CRwHmYJA0M8",
]
THEME_STEPS = ["sakura", "synthwave", "paper", "matrix", rs.THEME]

OVERLAY_JS = r"""
(() => {
  if (location.port !== '8080') return;
  const hideCss = '.pending-changes-host{display:none!important}';
  const addStyle = (css) => { const s = document.createElement('style'); s.textContent = css; document.head.appendChild(s); };
  const onReady = (fn) => document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', fn) : fn();
  onReady(() => addStyle(hideCss));
  if (window !== window.top) return;

  const seed = __SEED__;
  if (!sessionStorage.getItem('demoSeeded')) {
    sessionStorage.setItem('demoSeeded', '1');
    for (const [k, v] of Object.entries(seed)) localStorage.setItem(k, v);
  }

  onReady(() => {
    // Same reveal the Electron preload does, so the Spaces titlebar shows.
    document.body.classList.add('is-electron');
    const bar = document.getElementById('shellTitlebar');
    if (bar) bar.hidden = false;
    // Pane iframes carry "Cuttle Content" (promoted to data-tooltip), which tips under the cursor.
    setInterval(() => document.querySelectorAll('iframe[data-tooltip]').forEach((f) => f.removeAttribute('data-tooltip')), 250);

    addStyle(`
      #demo-cursor{position:fixed;left:0;top:0;width:28px;height:28px;z-index:2147483647;pointer-events:none;
        filter:drop-shadow(0 2px 3px rgba(0,0,0,.5));transform-origin:3px 2px;transition:scale .08s}
      #demo-cursor.down{scale:.82}
      .demo-ripple{position:fixed;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;z-index:2147483646;
        pointer-events:none;border:3px solid rgba(167,139,250,.95);animation:demo-ripple .5s ease-out forwards}
      @keyframes demo-ripple{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}
      #demo-caption{position:fixed;left:50%;top:52px;transform:translateX(-50%);z-index:2147483645;pointer-events:none;
        padding:9px 18px;border-radius:999px;background:rgba(18,14,36,.86);color:#fff;
        font:600 16px/1.2 system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.4);opacity:0;transition:opacity .25s}
      #demo-keys{position:fixed;z-index:2147483647;pointer-events:none;padding:4px 9px;border-radius:7px;
        background:#fff;color:#1b1530;font:700 13px system-ui,sans-serif;box-shadow:0 3px 10px rgba(0,0,0,.4);
        opacity:0;transition:opacity .15s}
    `);
    const cursor = document.createElement('div');
    cursor.id = 'demo-cursor';
    cursor.innerHTML = '<svg viewBox="0 0 28 28" width="28" height="28"><path d="M3 2 L3 22 L8.5 16.8 L12.3 25.5 L15.8 24 L12 15.5 L19.5 15.5 Z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    const caption = document.createElement('div');
    caption.id = 'demo-caption';
    const keys = document.createElement('div');
    keys.id = 'demo-keys';
    document.body.append(cursor, caption, keys);
    let cx = -100, cy = -100;
    window.__demo = {
      move(x, y) {
        cx = x; cy = y;
        cursor.style.transform = `translate(${x - 3}px, ${y - 2}px)`;
        keys.style.left = (x + 22) + 'px'; keys.style.top = (y + 22) + 'px';
      },
      down(on) { cursor.classList.toggle('down', !!on); },
      ripple() {
        const r = document.createElement('div');
        r.className = 'demo-ripple'; r.style.left = cx + 'px'; r.style.top = cy + 'px';
        document.body.appendChild(r); setTimeout(() => r.remove(), 600);
      },
      caption(text) {
        if (text) caption.textContent = text;
        caption.style.opacity = text ? '1' : '0';
      },
      keys(text) {
        if (text) keys.textContent = text;
        keys.style.opacity = text ? '1' : '0';
      },
    };
    window.__demo.move(cx, cy);
  });
})();
"""


def write_webm(path: Path, frames: list[Image.Image], fps: int, hold: int = 0) -> None:
    """Unframed constant-fps WebM (VP8) plus a poster PNG, for the product site."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Playwright's bundled ffmpeg has no rawvideo demuxer or pipe: protocol, so feed it an
    # MJPEG stream from a file.
    stream = path.with_suffix(".mjpeg")
    with stream.open("wb") as fh:
        for im in frames + [frames[-1]] * hold:
            im.convert("RGB").save(fh, "JPEG", quality=95)
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(fps),
             "-i", str(stream), "-c:v", "libvpx", "-b:v", "0", "-crf", "12", "-qmin", "4", "-qmax", "30",
             "-auto-alt-ref", "0", "-pix_fmt", "yuv420p", str(path)],
            check=True)
    finally:
        stream.unlink(missing_ok=True)
    frames[0].save(path.with_suffix(".png"))
    print(f"wrote {path.relative_to(rs.REPO)} ({path.stat().st_size // 1024} KB)")


class Recorder:
    """Screencast frames plus the wall-clock windows that make it into the output."""

    def __init__(self, page, width: int = OUT_WIDTH, fps: int = FPS):
        self.cdp = page.context.new_cdp_session(page)
        self.fps = fps
        self.frames: list[tuple[float, str]] = []
        self.windows: list[tuple[float, float]] = []
        self._start: float | None = None
        self.cdp.on("Page.screencastFrame", self._on_frame)
        self.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 92, "maxWidth": width, "maxHeight": 2000, "everyNthFrame": 1,
        })

    def _on_frame(self, ev: dict) -> None:
        self.frames.append((time.time(), ev["data"]))
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

    def start(self) -> None:
        self._start = time.time()

    def stop(self) -> None:
        if self._start is not None:
            self.windows.append((self._start, time.time()))
        self._start = None

    def save(self, path: Path, *, crossfade: int = 0, quality: int = 72, backdrop=None,
             raw_video: Path | None = None) -> None:
        self.cdp.send("Page.stopScreencast")
        times = [t for t, _ in self.frames]
        segments = []
        for a, b in self.windows:
            seg, t = [], a
            while t < b:
                seg.append(max(bisect.bisect_right(times, t) - 1, 0))
                t += 1 / self.fps
            segments.append(seg)

        cache: dict[int, Image.Image] = {}

        def img(i: int) -> Image.Image:
            if i not in cache:
                cache[i] = Image.open(io.BytesIO(base64.b64decode(self.frames[i][1]))).convert("RGB")
            return cache[i]

        out: list[tuple[object, Image.Image]] = []
        for si, seg in enumerate(segments):
            prev = out[-1][1] if out and crossfade else None
            for fi, idx in enumerate(seg):
                if prev is not None and fi < crossfade:
                    alpha = (fi + 1) / (crossfade + 1)
                    out.append((("blend", si, fi), Image.blend(prev, img(idx), alpha)))
                else:
                    out.append((idx, img(idx)))

        if raw_video is not None:
            write_webm(raw_video, [im for _, im in out], self.fps, hold=round(1.2 * self.fps))

        images, durations = [], []
        frame_ms = round(1000 / self.fps)
        for key, im in out:
            if images and key == last_key:
                durations[-1] += frame_ms
                continue
            images.append(im)
            durations.append(frame_ms)
            last_key = key
        durations[-1] += 1200
        if backdrop is not None:
            images = [backdrop.compose(im) for im in images]
        images[0].save(path, save_all=True, append_images=images[1:], duration=durations,
                       loop=0, quality=quality, method=4)
        secs = sum(durations) / 1000
        print(f"wrote {path.relative_to(rs.REPO)} — {len(images)} frames, {secs:.1f}s, "
              f"{path.stat().st_size // 1024} KB")


class Demo:
    """Moves the real mouse and the drawn cursor together."""

    def __init__(self, page):
        self.page = page
        self.x, self.y = VIEWPORT["width"] * 0.6, VIEWPORT["height"] * 0.6
        self._sync()

    def _sync(self) -> None:
        self.page.evaluate("([x, y]) => window.__demo && __demo.move(x, y)", [self.x, self.y])

    def move(self, x: float, y: float, dur: float = 0.55) -> None:
        x0, y0 = self.x, self.y
        steps = max(6, int(dur * 45))
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)
            self.x, self.y = x0 + (x - x0) * e, y0 + (y - y0) * e
            self.page.mouse.move(self.x, self.y)
            self._sync()

    def click(self, x: float, y: float, modifier: str | None = None, label: str | None = None) -> None:
        self.move(x, y)
        if modifier:
            self.keys(label or modifier)
            self.page.keyboard.down(modifier)
            self.wait(0.45)
        self.page.evaluate("__demo.down(true)")
        self.page.mouse.down()
        self.page.mouse.up()
        self.page.evaluate("__demo.down(false); __demo.ripple()")
        if modifier:
            self.wait(0.2)
            self.page.keyboard.up(modifier)
            self.keys(None)

    def drag(self, x0: float, y0: float, x1: float, y1: float, dur: float = 0.8) -> None:
        self.move(x0, y0)
        self.wait(0.15)
        self.page.evaluate("__demo.down(true)")
        self.page.mouse.down()
        self.move(x1, y1, dur)
        self.page.mouse.up()
        self.page.evaluate("__demo.down(false)")

    def caption(self, text: str | None) -> None:
        self.page.evaluate("(t) => __demo.caption(t)", text)

    def keys(self, text: str | None) -> None:
        self.page.evaluate("(t) => __demo.keys(t)", text)

    def wait(self, secs: float) -> None:
        self.page.wait_for_timeout(int(secs * 1000))

    def rects(self, selector: str) -> list[dict]:
        return self.page.evaluate(
            """(sel) => [...document.querySelectorAll(sel)].map((e) => {
                const r = e.getBoundingClientRect();
                return {x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2,
                        text: e.textContent.trim()};
            }).filter((r) => r.w > 0 && r.h > 0)""",
            selector,
        )


_ids = iter(range(1, 10_000))


def leaf(chat: int | None = None, page: str = "/chat_page.html", flex: str = "") -> dict:
    node = {"type": "leaf", "id": f"demo-leaf-{next(_ids)}", "page": page, "flex": flex}
    if chat:
        node["page"] = f"/chat_page.html?chat={chat}"
        node["chat"] = str(chat)
    return node


def group(orientation: str, children: list[dict], flex: str = "") -> dict:
    return {"type": "group", "id": f"demo-group-{next(_ids)}", "orientation": orientation,
            "flex": flex, "children": children}


def shell_seed(spaces: list[tuple[str, dict]], extra: dict | None = None) -> dict:
    ids = [f"sp_demo_{i}" for i in range(len(spaces))]
    state = {"active": ids[0], "spaces": [
        {"id": sid, "name": name, "root": root} for sid, (name, root) in zip(ids, spaces)]}
    seed = {
        "shell_spaces_v1": json.dumps(state),
        "shell_split_layout": json.dumps({"version": 2, "root": spaces[0][1]}),
        "theme": rs.THEME,
        "cuttleVideoBackgroundEnabled": "0",
    }
    seed.update(extra or {})
    return seed


def open_shell(browser, auth_state: dict, base: str, seed: dict, viewport: dict = VIEWPORT, scale: float = 1):
    ctx = browser.new_context(ignore_https_errors=True, viewport=viewport, storage_state=auth_state,
                              device_scale_factor=scale)
    ctx.route("**/api/**", lambda route: route.fulfill(json={"success": True})
              if route.request.method != "GET" else route.continue_())
    # Keep the real install's wallpaper playlists out of the capture.
    demo_wallpaper = {"success": True, "video_background": {
        "playlists": {"default": DEMO_VIDEOS}, "active_playlist": "default", "urls": DEMO_VIDEOS,
        "duration": 600, "opacity": 40, "enabled": True}}
    ctx.route("**/api/settings/video-background", lambda route: route.fulfill(json=demo_wallpaper)
              if route.request.method == "GET" else route.fulfill(json={"success": True}))
    ctx.add_init_script(OVERLAY_JS.replace("__SEED__", json.dumps(seed)))
    page = ctx.new_page()
    page.goto(f"{base}/", wait_until="networkidle")
    page.wait_for_timeout(3000)
    for frame in page.frames:
        try:
            frame.evaluate(rs.SCROLL_CHAT_TOP)
        except Exception:
            pass
    return page


def backdrop(browser, scene: str):
    return promo.Composer(browser).backdrop(FRAMES[scene], VIEWPORT["width"] / VIEWPORT["height"])


def record_customize(browser, auth_state: dict, base: str, chats: dict) -> None:
    seed = shell_seed(
        [("Build", group("horizontal", [
            leaf(page="/settings_page.html", flex="0.85 1 0%"), leaf(chats["hero"], flex="1.15 1 0%")]))],
        {
            "cuttleVideoBackgroundEnabled": "1",
            "cuttleVideoBackgroundList": json.dumps(DEMO_VIDEOS),
            "cuttleVideoBackgroundOpacity": "40",
            "cuttleVideoBackgroundBlendTarget": "black",
            "cuttleVideoBackgroundDuration": "600",
        },
    )
    page = open_shell(browser, auth_state, base, seed)
    settings = next(f for f in page.frames if "settings_page" in f.url)
    settings.evaluate("document.getElementById('themeSelect').scrollIntoView({block: 'start'}); scrollBy(0, -56)")
    page.wait_for_timeout(6000)

    demo = Demo(page)
    rec = Recorder(page, fps=10)
    select = settings.locator("#themeSelect")
    play_buttons = settings.locator(".video-play-btn")

    rec.start()
    demo.caption("Themes and video wallpapers, applied live")
    demo.wait(2.2)
    rec.stop()
    for i, theme in enumerate(THEME_STEPS):
        btn = play_buttons.nth((i + 1) % len(DEMO_VIDEOS)).bounding_box()
        rec.start()
        demo.click(btn["x"] + btn["width"] / 2, btn["y"] + btn["height"] / 2)
        demo.wait(0.3)
        rec.stop()
        demo.wait(5.5)
        box = select.bounding_box()
        rec.start()
        demo.click(box["x"] + box["width"] * 0.6, box["y"] + box["height"] / 2)
        select.select_option(theme)
        demo.wait(1.6)
        rec.stop()
    rec.save(OUT / "customize.webp", crossfade=3, quality=58, backdrop=backdrop(browser, "customize"),
             raw_video=rs.RAW / "customize.webm")
    page.context.close()


def record_multiplex(browser, auth_state: dict, base: str, chats: dict) -> None:
    seed = shell_seed([
        ("Build", leaf(chats["hero"])),
        ("Research", group("horizontal", [
            leaf(chats["cost"], flex="1.1 1 0%"),
            group("vertical", [leaf(chats["standup"]), leaf(page="/dashboards_page.html")], flex="0.9 1 0%"),
        ])),
        ("Ops", group("horizontal", [leaf(chats["render"]), leaf(chats["standup"]), leaf(chats["cost"])])),
    ])
    page = open_shell(browser, auth_state, base, seed)
    for name in ("Research", "Ops", "Build"):
        page.locator(".shell-space-tab", has_text=name).click()
        page.wait_for_timeout(2500)
    page.wait_for_timeout(1500)
    for frame in page.frames:
        try:
            frame.evaluate(rs.SCROLL_CHAT_TOP)
        except Exception:
            pass
    demo = Demo(page)
    rec = Recorder(page)

    def switch_space(name: str) -> None:
        tab = next(t for t in demo.rects(".shell-space-tab") if t["text"].startswith(name))
        demo.click(tab["cx"], tab["cy"])
        demo.wait(0.25)
        rec.stop()
        demo.wait(1.8)
        rec.start()

    rec.start()
    demo.caption("Split any pane")
    demo.wait(0.8)
    logo = demo.rects(".rail-logo-clickable")[0]
    demo.click(logo["cx"], logo["cy"])
    demo.wait(1.6)
    logo = demo.rects(".rail-logo-clickable")[-1]
    demo.click(logo["cx"], logo["cy"], modifier="Alt", label="Alt + click")
    demo.wait(1.6)

    demo.caption("Drag dividers to resize")
    handles = demo.rects(".split-resize-handle")
    v = max(handles, key=lambda h: h["h"])
    demo.drag(v["cx"], v["cy"], v["cx"] - 180, v["cy"])
    demo.wait(0.3)
    demo.drag(v["cx"] - 180, v["cy"], v["cx"] + 140, v["cy"])
    demo.wait(0.4)
    handles = demo.rects(".split-resize-handle")
    h = max(handles, key=lambda r: r["w"])
    demo.drag(h["cx"], h["cy"], h["cx"], h["cy"] - 180)
    demo.wait(0.8)

    demo.caption("Each space keeps its own layout")
    for name in ("Research", "Ops"):
        switch_space(name)
        demo.wait(0.6)
        handles = demo.rects(".split-resize-handle")
        if handles:
            hd = max(handles, key=lambda r: r["h"])
            demo.drag(hd["cx"], hd["cy"], hd["cx"] + 160, hd["cy"])
            demo.wait(0.8)
    switch_space("Build")
    demo.wait(1.2)
    demo.caption(None)
    demo.wait(0.4)
    rec.stop()
    rec.save(OUT / "multiplex.webp", crossfade=2, quality=75, backdrop=backdrop(browser, "multiplex"),
             raw_video=rs.RAW / "multiplex.webm")
    page.context.close()


SCENES = {"customize": record_customize, "multiplex": record_multiplex}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scenes", nargs="*", metavar="scene", help=f"any of {', '.join(SCENES)} (default: all)")
    ap.add_argument("--base", default="https://127.0.0.1:8080")
    ap.add_argument("--keep", action="store_true", help="Leave the demo chats in place")
    args = ap.parse_args()
    unknown = set(args.scenes) - set(SCENES)
    if unknown:
        ap.error(f"unknown scene(s): {', '.join(sorted(unknown))}")
    OUT.mkdir(parents=True, exist_ok=True)
    db = rs.get_auth_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(ignore_https_errors=True, viewport=VIEWPORT)
        user_id = rs.guest_login(ctx, args.base)
        auth_state = ctx.storage_state()
        ctx.close()
        chats = rs.seed_demo_chats(db, user_id)
        try:
            for name in args.scenes or SCENES:
                SCENES[name](browser, auth_state, args.base, chats)
        finally:
            browser.close()
            if not args.keep:
                for sid in chats.values():
                    db.delete_chat_session(sid, user_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
