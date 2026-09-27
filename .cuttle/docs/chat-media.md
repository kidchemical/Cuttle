# Chat media (images / video)

Share screenshots and clips in chat as **markdown images** (or `<media>`).
Cuttle stages a copy under `src/output/shared/`, serves it at `/output/shared/…`,
and shows a clickable preview. Click opens a **lightbox** that covers the full
app shell (all split panes), not just the chat column. ←/→ for multiple items
in that bubble, Esc to close, Download for phone/LAN. Zoom with **+/−**,
pinch (phone), scroll wheel, or the on-screen controls; the center button
toggles **Fit** (default / contain) vs **Actual** (1:1 pixels). Shortcuts:
`+`/`-`, `0` = fit, `1` = actual, double-click toggles. Drag / pinch-pan when
zoomed. Zoom-out floor is **25%** of natural size (or Fit, if Fit is smaller).

Right-click an **image** thumb (markdown / attachment) or the lightbox image for
**Copy** (PNG on the system clipboard) and **Save** (same as Download /
`?download=1`). Videos keep the browser default menu.

Videos also get a mid-frame poster (`*.poster.jpg`, via ffmpeg) used as the
chat thumb with a play overlay. Missing posters are generated lazily on first
request to `/output/shared/<name>.poster.jpg`.

Staging also writes a `*.meta.json` sidecar with the **original filename and
path**. The lightbox caption shows that name + path when you open a preview,
plus any **description** you attach (see below).

## Agent recipe

1. Write or copy the file somewhere durable (project `temp/`, build output, etc.).
2. Prefer a hosted URL in the reply:

```markdown
![UI after fix](/output/shared/ui-after-fix.png)
```

Or let Cuttle auto-stage a local path on persist:

```markdown
![UI after fix](/path/to/Cuttle/temp/ui-after-fix.png)
```

### Descriptions (optional)

Add a short label **and** a longer description when context helps (what changed,
what to look at, why it matters). Descriptions show under the thumb in chat and
in the lightbox caption bar.

Markdown title string (standard `![alt](url "description")`):

```markdown
![UI after fix](/output/shared/ui-after-fix.png "Close and Download stay clear of the S22 status bar and gesture nav.")
```

`<media>` attribute:

```xml
<media type="video" src="/output/shared/demo.mp4" title="Walkthrough"
  description="30s clip of the lightbox on Galaxy S22 after the safe-area patch."/>
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

3. Do **not** rely on `file://` for in-chat previews — browsers and phones cannot
   load host disk paths from the HTTPS chat page.

## Staging API (optional)

```http
POST /api/shared-media/stage
{"path": "/path/to/Cuttle/temp/shot.png"}
```

→ `{ "success": true, "url": "/output/shared/shot-….png", "kind": "image" }`

Python: `api.shared_media.stage_file(path)`.

## Expiry

Staged copies expire after **7 days** (mtime). Override with
`CUTTLE_SHARED_MEDIA_TTL_DAYS`. Purge runs on:

- Cuttle daemon start
- Flask API start
- Electron **host** start (not client mode)

Original files outside `src/output/shared/` are never deleted by purge.

## Download

Lightbox **Download** (and the image context-menu **Save**) uses
`/output/…?download=1` (`Content-Disposition: attachment`) so phones can save
the file. **Copy** puts a PNG on the system clipboard for pasting into other apps.
