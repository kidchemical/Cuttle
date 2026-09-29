# Git (global)

## `git checkout --` / restore is not “undo my last edit”

`git checkout -- path` (and `git restore path`) resets the **whole file** to `HEAD`. It deletes **every** uncommitted hunk in that file — not just the change you meant to reverse.

**What went wrong (CH-000406):** user asked to undo a YouTube/mobile wallpaper experiment. Agent ran `git checkout --` on shared dirty files (`app_shell.js`, `chat_page.html`, `video_background.*`, …). That wiped unrelated pending work in the same files.

**Do instead:** reverse only the target hunks (edit/StrReplace/`git apply -R` on a scoped patch). Use checkout/restore only when you are certain the file’s full dirty diff should die.

## `git push` — Cuttle chat is form-gated (never the agent shell)

**Never** run `git push` (or `git push --force`) from an agent shell in Cuttle chat. The Git pending-changes **UI** is the user’s path. In chat, emit the `git.push` action form and stop. Click runs `.cuttle_global/actions/git-push.yaml` (no LLM).

Do not ask “OK to push?” in prose. Example card (only emit the tagged JSON block, not this filename in backticks next to an open tag):

```text
<cuttle_action_form>
{"mode":"choice","title":"Push to remote?","lock":"form","silent":true,"options":[
 {"id":"status","label":"Status — show remote + branch (no push)","action":"git.push","params":{"mode":"status"}},
 {"id":"push","label":"Push origin main","action":"git.push","params":{"mode":"push","remote":"origin","branch":"main"}},
 {"id":"no","label":"Don't push"}
]}
</cuttle_action_form>
```

Adjust `remote` / `branch` / `path` to the repo in the chat project chip. Never force-push from this action.


