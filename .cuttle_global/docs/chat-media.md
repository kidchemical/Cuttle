# Chat media (images / video / audio)

Share screenshots, video clips, and audio clips in chat as **markdown images**
(or `<media>`). Cuttle stages a copy under `<home>/output/shared/`, serves it at
`/output/shared/…`, and shows a clickable preview. Click opens a **lightbox**
that covers the full app shell (all split panes), not just the chat column.
←/→ for multiple items in that bubble, Esc to close, Download for phone/LAN.
Zoom with **+/−**, pinch (phone), scroll wheel, or the on-screen controls; the
center button toggles **Fit** (default / contain) vs **Actual** (1:1 pixels).
Shortcuts: `+`/`-`, `0` = fit, `1` = actual, double-click toggles. Drag /
pinch-pan when zoomed. Zoom-out floor is **25%** of natural size (or Fit, if
Fit is smaller). Audio items skip zoom and show a native audio player in the
lightbox (autoplay + controls).

Right-click an **image** thumb (markdown / attachment) or the lightbox image for
**Copy** (PNG on the system clipboard) and **Save** (same as Download /
`?download=1`). Videos and audio keep the browser default menu.

Videos also get a mid-frame poster (`*.poster.jpg`, via ffmpeg) used as the
chat thumb with a play overlay. Missing posters are generated lazily on first
request to `/output/shared/<name>.poster.jpg`. Audio thumbs are a compact play
chip with the filename (no poster).

Staging also writes a `*.meta.json` sidecar with the **original filename and
path**. The lightbox caption shows that name + path when you open a preview,
plus any **description** you attach (see below).

Composer **voice-clip attachments** (mic button) stay on the upload path with
an inline `<audio>` player in the bubble — see `CHAT_ATTACHMENTS.md`. This
runbook is the agent shared-media recipe.

## Agent recipe

1. Write or copy the file somewhere durable (project `temp/`, build output, etc.).
2. **Stage local media explicitly before replying** with the agent-ops CLI
   (Cuttle venv + `PYTHONPATH=src` from `agent-ops-cli.md`). Pass the real
   filesystem path (spaces intact) — not a URL-encoded path.
3. Require exit `0` / `success: true`, and use the returned `url` verbatim in the
   Markdown image or `<media>` tag. Staging adds a filename suffix; do not invent
   the hosted URL.

```bash
PYTHONPATH=src .venv/bin/python -m api.shared_media stage \
  /path/to/project/temp/before-after.png --json
```

→ `{ "success": true, "url": "/output/shared/before-after-….png", "kind": "image", … }`

Without `--json`, stdout is just the URL (easy to paste into the reply):

```bash
URL=$(PYTHONPATH=src .venv/bin/python -m api.shared_media stage \
  /path/to/project/temp/before-after.png)
```

For example, if the returned URL is `/output/shared/before-after-abc123.png`:

```markdown
![Before and after](/output/shared/before-after-abc123.png)
```

**Do not rely on local-path auto-staging for delivery.** A local path with
URL-encoded spaces can produce a dead link; explicitly stage it and use the
returned `/output/shared/…` URL. A file
existing on disk or appearing in a tool preview does not verify the chat attachment.
Local-path auto-staging remains a compatibility feature, not the agent delivery recipe.

Do **not** import `api.shared_media` from a throwaway agent script — use
`python -m api.shared_media` above (same pattern as the rest of `agent-ops-cli.md`).

### Linking other Cuttle-served files (reports, HTML)

A file under `<home>/output/` is served at `/output/…` to any signed-in client.
Link it with the relative path, for example `[Trim plan](/output/reports/plan.html)`:
chat turns it into a link on whatever address the user is on (localhost, LAN,
Tailscale). Never hardcode a host or IP; the agent cannot see how the user is
connected.

### Descriptions (optional)

Add a short label **and** a longer description when context helps (what changed,
what to look at, why it matters). Descriptions show under the thumb in chat and
in the lightbox caption bar.

Markdown title string (standard `![alt](url "description")`):

```markdown
![UI after fix](/output/shared/ui-after-fix.png "Close and Download stay clear of the S22 status bar and gesture nav.")
```

> Avoid parentheses `(...)` inside the Markdown `"description"` title string: the chat parser matches `!\[...\]\(([^)]+)\)` and stops at the first `)`, which breaks the image. Use `<media>` (below) if the description needs parentheses.

`<media>` attribute:

```xml
<media type="video" src="/output/shared/demo.mp4" title="Walkthrough"
  description="30s clip of the lightbox on a phone after the safe-area patch."/>
```

Or body text (better for longer copy):

```xml
<media type="image" src="/output/shared/ui-after-fix.png" title="UI after fix">
Close button cleared the status bar; Download sits above the gesture nav.
</media>
```

- `title` / markdown alt → short label (filename still preferred in the lightbox title when meta exists)
- `description` / `desc` / markdown `"…"` / `<media>` body → context line
- Skip the description when the image is self-explanatory

Video without description:

```markdown
![Walkthrough](/output/shared/demo.mp4)
```

```xml
<media type="video" src="/output/shared/demo.mp4" title="Walkthrough"/>
```

Audio (``.mp3`` / ``.wav`` / ``.m4a`` / ``.flac`` / ``.aac`` / ``.opus`` /
``.oga``). Markdown image syntax or `<media type="audio">` both open the
lightbox player:

```markdown
![Narration](/output/shared/narration-abc123.mp3)
```

```xml
<media type="audio" src="/output/shared/narration-abc123.mp3" title="Narration"
  description="Agent walkthrough of the fix."/>
```

Do **not** use `file://` or filesystem paths as final image URLs — browsers and
phones cannot load host disk paths from the HTTPS chat page.

## Staging CLI

```bash
PYTHONPATH=src .venv/bin/python -m api.shared_media stage /path/to/shot.png --json
PYTHONPATH=src .venv/bin/python -m api.shared_media meta /output/shared/shot-….png --json
PYTHONPATH=src .venv/bin/python -m api.shared_media purge --json
```

→ `{ "success": true, "url": "/output/shared/shot-….png", "kind": "image" }`

(`kind` is `image`, `video`, or `audio`.)

In-process code (Flask, `media_generate --share`, mesh render delivery) calls
`api.shared_media.stage_file` directly; the HTTP mirror is
`POST /api/shared-media/stage`. Those are host internals, not the agent recipe.

## Repairing a broken attachment

Check the original file still exists, stage it again with
`python -m api.shared_media stage …`, verify exit `0` / `success: true`, then
resend with the returned URL. If a hosted copy has expired, restage the
original. Do not resend an unchanged local-path link or guess that replacing
spaces with `%20` will repair delivery. If staging fails, report the failure
instead of claiming the attachment is fixed.

## Expiry

Staged copies expire after **7 days** (mtime). Override with
`CUTTLE_SHARED_MEDIA_TTL_DAYS`. Purge runs on:

- Cuttle daemon start
- Flask API start
- Electron **host** start (not client mode)

Original files outside `<home>/output/shared/` are never deleted by purge.

## Download

Lightbox **Download** (and the image context-menu **Save**) uses
`/output/…?download=1` (`Content-Disposition: attachment`) so phones can save
the file. **Copy** puts a PNG on the system clipboard for pasting into other apps.
