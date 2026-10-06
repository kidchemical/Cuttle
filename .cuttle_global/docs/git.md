# Git (global)

## `git checkout --` / restore is not “undo my last edit”

`git checkout -- path` (and `git restore path`) resets the **whole file** to `HEAD`. It deletes **every** uncommitted hunk in that file — not just the change you meant to reverse.

**What went wrong (CH-000406):** user asked to undo a YouTube/mobile wallpaper experiment. Agent ran `git checkout --` on shared dirty files (`app_shell.js`, `chat_page.html`, `video_background.*`, …). That wiped unrelated pending work in the same files.

**Do instead:** reverse only the target hunks (edit/StrReplace/`git apply -R` on a scoped patch). Use checkout/restore only when you are certain the file’s full dirty diff should die.

## Commits Cuttle makes

- **Pending-changes panel:** **Commit** with an empty message (or the quick
  **Commit** button) suggests a message and commits in one click — there is no
  review step. Type or edit the message first if you want to see it.
- **Auto-commit (opt-in, default off):** Settings → Agents → Git →
  *Auto-commit agent edits* (`git_auto_commit`; owner `api.git_autocommit`).
  After each successful agent turn, Cuttle commits only the files that turn
  changed (edit-attribution journal for that run), with a suggested message and
  `Cuttle-Attributed` trailers. Other dirty files stay pending; a file the agent
  touched is committed whole. Never pushes; a toast in the chat reports it.

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

**Release tags:** push one tag with `"params":{"mode":"push","remote":"origin","tag":"vX.Y.Z"}`.
It pushes only `refs/tags/vX.Y.Z` and refuses a tag that does not exist locally.
Never suggest `git push --tags` or `--all`: old local tags and branches can hold
history that must stay private.



## Push-blocker reports and database inspection

Cuttle's pre-push hook emits a redacted structured report. Git push failures in
chat, the Git page, and push action cards open a report popup naming each check,
commit, offending file, line, and (for SQLite) table, column, and row position.
The toast identifies the blocking hook rather than Git's generic stderr footer.
Source/value previews are redacted. “View flagged commit diff” opens the existing
shared diff popup only on an explicit click; that local authenticated view can
show original secret material. Database cell values remain redacted in reports.

The checks are `path-policy`, `secret-patterns`, `gitleaks` (when installed), and
`sqlite-secrets`. Every introduced commit version is scanned, including a secret
removed by a later commit. Gitleaks errors are reported as coverage warnings;
built-in checks still run.

SQLite inspection covers changed committed `.db`, `.db3`, `.sqlite`, and
`.sqlite3` blobs, not live runtime databases or ignored/untracked files. It scans
text/blob cells for secret patterns and flags nonempty credential columns.
Values never enter the report. Inspection is read-only and in memory, with
extension loading disabled. Bounds: 16 MiB per database, 100,000 rows, three
seconds, and 1,000 findings. Unsupported formats (including WAL fragments),
encrypted/unreadable databases, and limit violations block with an inspection
reason. This is a conservative credential gate, not proof that a clean database
contains no private information.

Code owners: `.cuttle/scripts/git-hooks/pre-push` (project policy),
`core.git_push_diagnostics` (inspection/report parsing), `api.git_routes`
(transport), `CuttleGitPushReport` (shared popup). Reviewed fixture/placeholder exceptions live in
`.cuttle/scripts/git-hooks/scanner-exceptions.json`. Each matches an exact commit,
file blob, line number, line digest, scanner, and rule, with a review reason.
Both built-in and gitleaks findings use this policy. Other commits, changed
content, wider findings, path-policy findings, and SQLite findings stay blocked.
The hook prints the number of approved findings; it does not disable scanners.
