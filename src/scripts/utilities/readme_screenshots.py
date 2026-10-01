"""Capture README screenshots (WebP) of the running web UI into docs/media/.

Signs in as the reusable Cuttle Demo guest (so no real chats appear), seeds demo chats with
fake content, captures them in the light theme at 2x, frames each capture as a
landing-page style promo (see readme_promo.py), then soft-deletes the demo chats.

    .venv/bin/python src/scripts/utilities/readme_screenshots.py [--base URL] [--keep] [--raw]

Needs a running Flask (default https://127.0.0.1:8080), Playwright Chromium, and
network access to Google Fonts (Inter) for the frame typography.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parents[2]
REPO = SRC.parent
sys.path.insert(0, str(SRC))

from playwright.sync_api import sync_playwright  # noqa: E402

from api.auth_db import get_auth_db  # noqa: E402
from api.fixture_accounts import fixture_browser_auth  # noqa: E402
from api.chat_widgets import rewrite_assistant_text_widgets  # noqa: E402
from api.project_actions import prepare_assistant_text_for_actions  # noqa: E402

import readme_promo as promo  # noqa: E402

OUT = REPO / "docs" / "media"
RAW = REPO / "temp" / "readme_raw"
# Lavender Dream: a light theme whose purple accents match the brand.
THEME = "lavender"
SCALE = 2
VIEWPORT = {"width": 1440, "height": 900}
CHAT_VIEWPORT = {"width": 1180, "height": 900}
DASH_VIEWPORT = {"width": 1280, "height": 860}
PHONE_VIEWPORT = {"width": 390, "height": 844}
DEMO_PROJECT = {"project_name": "Demo App", "project_path": "/home/demo/demo-app"}

# Keeps the capture about the demo: the install's real git state and stray
# hover tooltips are not part of the product shot.
CAPTURE_CSS = ".pending-changes-host{display:none!important}"
CAPTURE_INIT = """(() => {
  localStorage.setItem('theme', '%s');
  localStorage.setItem('cuttleVideoBackgroundEnabled', '0');
  if (location.port !== '8080') return;
  const add = () => { const s = document.createElement('style'); s.textContent = '%s'; document.head.appendChild(s); };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', add) : add();
})();""" % (THEME, CAPTURE_CSS)


def _chip(label: str, meta: str, category: str) -> dict:
    return {"slash_command": {"chips": [{"label": label, "meta": meta, "category": category}]}}


HERO_USER = "/cursor add rate limiting to the /api/upload endpoint and cover it with tests"
HERO_REPLY = """<think>
I'll read the upload route and the existing limiter config first.

Found the shared `Limiter` in `api/limits.py`; wiring the upload route to it and adding tests.
</think>

Uploads are now rate-limited to **10 per minute per user**, and the new tests pass.

- `api/upload.py` — the route uses the shared limiter, and over-limit requests get a `429` with a `Retry-After` header.
- `tests/test_upload_limits.py` — covers the happy path, the 11th request in a minute, and the reset after the window.

<cuttle_widget type="tasks" id="demo-upload-limits" title="Upload rate limiting"
  description="Harden the upload endpoint before the public beta.">
{"items":[
  {"id":"1","text":"Wire /api/upload to the shared limiter","done":true},
  {"id":"2","text":"Return 429 + Retry-After when over limit","done":true},
  {"id":"3","text":"Tests for limit + window reset","done":true},
  {"id":"4","text":"Expose the limit in settings","done":false}
]}
</cuttle_widget>

The limit is hard-coded for now. Should I move it into settings?

<cuttle_action_form>
{"mode":"choice","title":"Upload limit — next step","resume":true,"options":[
 {"id":"settings","label":"Move the limit into settings.json (recommended)"},
 {"id":"per-role","label":"Different limits for owner vs guests"},
 {"id":"leave","label":"Leave it hard-coded"}
]}
</cuttle_action_form>"""

COST_USER = "how much did each agent cost me this week?"
COST_REPLY = """Cursor did most of the work this week, but Codex cost the most per task.

| Agent | Turns | Cost | Avg / turn |
|---|---:|---:|---:|
| Cursor Auto | 142 | $18.40 | $0.13 |
| Codex | 37 | $14.10 | $0.38 |
| Claude | 21 | $6.95 | $0.33 |
| Local (Ollama) | 64 | $0.00 | $0.00 |

<vega>
{"$schema":"https://vega.github.io/schema/vega-lite/v5.json","description":"Agent cost this week",
 "data":{"values":[{"agent":"Cursor Auto","cost":18.4},{"agent":"Codex","cost":14.1},{"agent":"Claude","cost":6.95},{"agent":"Local (Ollama)","cost":0}]},
 "width":560,"height":170,"mark":{"type":"bar","color":"#7c5cf0","cornerRadiusTopLeft":4,"cornerRadiusTopRight":4},
 "encoding":{"x":{"field":"agent","type":"nominal","sort":"-y","title":null,"axis":{"labelAngle":0}},"y":{"field":"cost","type":"quantitative","title":"USD"}}}
</vega>

Moving routine edits from Codex to Cursor Auto would have saved about **$9** this week."""


RENDER_USER = "/codex render shots 40–120 of the intro on the mesh"
RENDER_REPLY = """Split the render across three workers; frames 40–120 are done in **6m 12s**.

| Worker | Frames | Avg / frame |
|---|---:|---:|
| studio-pc | 38 | 7.9s |
| desktop | 27 | 11.2s |
| laptop | 16 | 18.4s |

Output is in `renders/intro/0040-0120/`."""

STANDUP_USER = "summarize yesterday's commits for standup"
STANDUP_REPLY = """**Yesterday (12 commits)**

- Upload endpoint is rate-limited, with tests
- Faster chat history search on large sessions
- Workers retry dropped render chunks automatically
- New Synthwave and Matrix themes

**Today:** move the upload limit into settings, then the settings page redesign."""

REVIEW_USER = "/muse review the upload limiter branch before I merge"
REVIEW_REPLY = """Two things to fix before merging; the rest looks good.

1. **Guests share one bucket.** `current_user_id()` returns `None` for guest sessions, so everyone behind the same NAT hits the same limit. Key guests on their session id instead.
2. **`Retry-After` is in milliseconds.** The header must be whole seconds.

```diff
- resp.headers["Retry-After"] = str(window_ms)
+ resp.headers["Retry-After"] = str(math.ceil(window_ms / 1000))
```

The tests cover the limit and the window reset. Add one for a guest session and this is ready to merge."""


def seed_demo_chats(db, user_id: int) -> dict[str, int]:
    cursor_meta = _chip("Cursor - Auto", "/cursor · requested Auto", "cursor")
    router_meta = _chip("Router → Cursor", "agent router · picked Cursor Auto", "cursor")
    codex_meta = _chip("Codex", "/codex", "codex")
    muse_meta = _chip("Muse Code", "/muse", "muse")
    return {
        "review": seed_chat(db, user_id, "Review upload limiter", [
            ("user", REVIEW_USER, muse_meta), ("assistant", REVIEW_REPLY, muse_meta)]),
        "hero": seed_chat(db, user_id, "Rate-limit uploads", [
            ("user", HERO_USER, cursor_meta), ("assistant", HERO_REPLY, cursor_meta)]),
        "cost": seed_chat(db, user_id, "Agent costs this week", [
            ("user", COST_USER, {}), ("assistant", COST_REPLY, router_meta)]),
        "render": seed_chat(db, user_id, "Render intro shots", [
            ("user", RENDER_USER, codex_meta), ("assistant", RENDER_REPLY, codex_meta)]),
        "standup": seed_chat(db, user_id, "Standup notes", [
            ("user", STANDUP_USER, {}), ("assistant", STANDUP_REPLY, router_meta)]),
    }


def seed_chat(db, user_id: int, title: str, turns: list[tuple[str, str, dict]]) -> int:
    sid = db.create_chat_session(user_id, title)
    for role, content, meta in turns:
        meta = {**DEMO_PROJECT, **meta}
        if role == "assistant":
            content = prepare_assistant_text_for_actions(
                content, session_id=f"db_session_{sid}", project_path=DEMO_PROJECT["project_path"]
            )
            content = rewrite_assistant_text_widgets(
                content, session_id=sid, project_path=DEMO_PROJECT["project_path"], user_id=user_id
            )
        db.add_message(sid, role, content, meta)
    return sid


SCROLL_CHAT_TOP = """() => {
    const els = [...document.querySelectorAll('*')].filter(
        (e) => e.scrollHeight > e.clientHeight + 40 && getComputedStyle(e).overflowY.match(/auto|scroll/));
    els.sort((a, b) => b.scrollHeight - a.scrollHeight);
    if (els[0]) els[0].scrollTop = 0;
}"""


FRAMES = {
    "hero": promo.Frame(
        layout="hero", palette="dawn", eyebrow="Harness of harnesses",
        headline="Every agent.<br><em>One cockpit.</em>",
        sub="Run Cursor, Codex, Claude, Muse Code, and OpenCode side by side, from your desktop, your phone, or Discord.",
        command="./start_cuttle.sh", dots=(0, 5)),
    "chat-hero": promo.Frame(
        palette="lilac", eyebrow="Visible work", label="CHAT &nbsp;/&nbsp; 01",
        headline="Agents that <em>show their work.</em>",
        sub="Progress notes, pinned task lists, and one-click decisions, right inside the turn."),
    "chat-charts": promo.Frame(
        palette="mint", eyebrow="Rich answers", label="INSIGHTS &nbsp;/&nbsp; 02",
        headline="Answers you can <em>read at a glance.</em>",
        sub="Tables and Vega-Lite charts render inline, straight from the agent's reply."),
    "dashboards": promo.Frame(
        palette="sky", eyebrow="Model benchmarks", label="BENCHMARKS &nbsp;/&nbsp; 05",
        headline="Know which model <em>earns its cost.</em>",
        sub="Cost, pass rate, and speed for every provider, in one interactive 3D view."),
}


def fresh_page(browser, auth_state: dict, viewport: dict = VIEWPORT, **ctx_args):
    # The shell restores the last-open chat from browser storage, so every shot
    # gets its own context seeded with only the demo cookie.
    ctx = browser.new_context(ignore_https_errors=True, viewport=viewport, storage_state=auth_state,
                              device_scale_factor=ctx_args.pop("device_scale_factor", SCALE), **ctx_args)
    ctx.add_init_script(CAPTURE_INIT)
    return ctx.new_page()


def scroll_chats_top(page) -> None:
    for frame in page.frames:
        try:
            frame.evaluate(SCROLL_CHAT_TOP)
        except Exception:
            pass


def open_chat(browser, auth_state: dict, base: str, chat_id: int, viewport: dict = CHAT_VIEWPORT,
              top: bool = True, **ctx_args):
    page = fresh_page(browser, auth_state, viewport, **ctx_args)
    page.goto(f"{base}/?chat={chat_id}", wait_until="networkidle")
    page.wait_for_timeout(2500)
    if top:
        scroll_chats_top(page)
    return page


def capture(page, name: str, park: tuple[float, float] | None = None) -> Image.Image:
    # Park the mouse on an empty stretch of the left rail so no tooltip shows.
    vw, vh = page.viewport_size["width"], page.viewport_size["height"]
    page.mouse.move(*(park or (20, vh - 260)))
    page.wait_for_timeout(1500)
    shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    page.context.close()
    RAW.mkdir(parents=True, exist_ok=True)
    shot.save(RAW / f"{name}.png")
    return shot


def capture_hero(browser, auth_state: dict, base: str, chats: dict) -> Image.Image:
    import readme_gifs as rg

    seed = rg.shell_seed([("Build", rg.group("horizontal", [
        rg.leaf(chats["hero"], flex="1.05 1 0%"), rg.leaf(chats["review"], flex="0.95 1 0%")]))])
    page = rg.open_shell(browser, auth_state, base, seed, scale=SCALE)
    page.wait_for_timeout(1500)
    return capture(page, "hero")


def capture_dashboards(browser, auth_state: dict, base: str) -> Image.Image:
    import readme_gifs as rg

    seed = rg.shell_seed([("Benchmarks", rg.leaf(page="/dashboards_page.html"))])
    page = rg.open_shell(browser, auth_state, base, seed, viewport=DASH_VIEWPORT, scale=SCALE)
    dash = next(f for f in page.frames if "dashboards_page" in f.url)
    dash.get_by_text("Model Benchmarks", exact=True).first.click()
    page.wait_for_timeout(3500)
    return capture(page, "dashboards")


def save_framed(composer: promo.Composer, name: str, shot: Image.Image, phone: Image.Image | None = None) -> None:
    spec = FRAMES[name]
    if phone is not None:
        spec.phone = phone
    path = OUT / f"{name}.webp"
    composer.still(spec, shot).save(path, quality=90, method=6)
    print(f"wrote {path.relative_to(REPO)} ({path.stat().st_size // 1024} KB)")


def reframe(browser) -> None:
    composer = promo.Composer(browser)
    phone = RAW / "phone.png"
    for name in FRAMES:
        if (RAW / f"{name}.png").exists():
            save_framed(composer, name, Image.open(RAW / f"{name}.png"),
                        Image.open(phone) if name == "hero" and phone.exists() else None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="https://127.0.0.1:8080")
    ap.add_argument("--keep", action="store_true", help="Leave the demo chats in place")
    ap.add_argument("--reframe", action="store_true",
                    help=f"Re-frame the last raw captures in {RAW.relative_to(REPO)} (no Flask needed)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.reframe:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            reframe(browser)
            browser.close()
        return 0

    db = get_auth_db()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(ignore_https_errors=True, viewport=VIEWPORT)
        with fixture_browser_auth(ctx, args.base, db=db) as user_id:
            auth_state = ctx.storage_state()
            ctx.close()
            chats = {}
            try:
                chats = seed_demo_chats(db, user_id)
                capture_hero(browser, auth_state, args.base, chats)
                capture(open_chat(browser, auth_state, args.base, chats["standup"], PHONE_VIEWPORT,
                                  device_scale_factor=3, is_mobile=True, has_touch=True),
                        "phone", park=(380, 600))
                capture(open_chat(browser, auth_state, args.base, chats["hero"], top=False), "chat-hero")
                capture(open_chat(browser, auth_state, args.base, chats["cost"]), "chat-charts")
                capture_dashboards(browser, auth_state, args.base)
                reframe(browser)
            finally:
                browser.close()
                if not args.keep:
                    for sid in chats.values():
                        db.delete_chat_session(sid, user_id)
    print(json.dumps({"demo_user_id": user_id, "chats": chats, "kept": args.keep}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
