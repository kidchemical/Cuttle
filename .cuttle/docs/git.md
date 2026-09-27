# Git (hub)

## `git checkout --` / restore is not “undo my last edit”

`git checkout -- path` (and `git restore path`) resets the **whole file** to `HEAD`. It deletes **every** uncommitted hunk in that file — not just the change you meant to reverse.

**What went wrong (CH-000406):** user asked to undo a YouTube/mobile wallpaper experiment. Agent ran `git checkout --` on shared dirty files (`app_shell.js`, `chat_page.html`, `video_background.*`, …). That wiped unrelated pending work in the same files.

**Do instead:** reverse only the target hunks (edit/StrReplace/`git apply -R` on a scoped patch). Use checkout/restore only when you are certain the file’s full dirty diff should die.
