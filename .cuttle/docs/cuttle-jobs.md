# Gitea @cuttle remote jobs

Trigger Cuttle from Gitea issue comments without exposing the agent host.

Full server install lives in the separate `cuttle-jobs` repo on your forge host
(deploy: `./deploy-cuttle-jobs.sh` from that repo).

## Flow

```
@cuttle investigate|solve  (issue comment)
  → Gitea webhook (HMAC)
  → cuttle-jobs queue (SQLite)
  → Cuttle daemon claim/poll (LAN Bearer token)
  → isolated git workspace + agent
  → Gitea comment / PR / labels
```

## Cuttle host (`src/.env`)

```env
CUTTLE_JOBS_ENABLED=1
CUTTLE_JOBS_BASE_URL=http://<jobs-host>:8091
CUTTLE_JOBS_WORKER_TOKEN=<same as the queue server token>
CUTTLE_JOBS_POLL_SECONDS=15
CUTTLE_JOBS_WORKSPACES=owner/repo=/path/to/workspace

# Commit identity on @cuttle solve (Gitea links avatars by email)
GITEA_AGENT_USERNAME=Cuttle
GITEA_COMMIT_AUTHOR_NAME=Cuttle
GITEA_COMMIT_AUTHOR_EMAIL=cuttle@localhost
```

Job commits set `GIT_AUTHOR_*` / `GIT_COMMITTER_*` for that `git commit` only (no
global `git config` change). Subjects use the same Pending Changes suggester
(OpenAI gpt-4o-mini → … → heuristic), with a `Fixes #N` body trailer.

### Dedicated workspace (required)

The path in `CUTTLE_JOBS_WORKSPACES` is **machine-owned**. Do not open it in the
Unity editor or commit there by hand. Jobs are serialized (one at a time), so a
single persistent working copy is enough — prefer a `git worktree` off your main
clone so LFS objects are shared:

```powershell
cd "\path\to\project\source"   # or wherever .git lives
git fetch origin
git worktree add \path\to\workspaces\project\source origin/dev/core
```

Point `CUTTLE_JOBS_WORKSPACES` at the directory that contains `.git` or Unity `source/.git`.

Restart the Cuttle daemon after changing `.env`.

## Behavior notes

| Concern | Behavior |
|---|---|
| Heartbeat | Background thread renews `/heartbeat` every ~90s **during** the agent turn (not only between jobs), so a 30‑minute lease cannot expire mid-run while the worker is alive. |
| New `cuttle/issue-N` | `git fetch` then branch from current `origin/dev/core` (never stale local `dev/core`). |
| Follow-up on same issue | `fetch` + checkout `origin/cuttle/issue-N` (remote branch kept). |
| Next different issue | Parks workspace (reset/clean + detach on `origin/dev/core`); switches to the next issue branch. Does **not** delete prior `cuttle/issue-*` remotes. |
| Webhook recursion | Ignores `cuttle` bot user, `<!-- cuttle-bot -->` marker, non-`@cuttle` text, and dedupes by `comment_id`. Ack comments cannot re-enqueue. |
| Gitea channel | Agent must **not** post mid-turn or emit web-chat forms (`cuttle_action_form`). Worker posts one sanitized comment (strips `<think>` + forms). Acks are stylized (e.g. `🛠️ On it — **solve** from @user · job \`N\``). |
| Commit author | `Cuttle` + `GITEA_COMMIT_AUTHOR_EMAIL` (not the host git user). |
| Commit subject | Same OpenAI/heuristic pipeline as Pending Changes; body includes `Fixes #N`. |
| Failures | Gitea comments distinguish **infrastructure** (dirty workspace, push rejected, missing base) vs **agent** failures, with retry vs permanent. |
| Concurrency | One job at a time — intentional with a single Unity workspace. |
| Jobs UI | Cuttle Jobs page (`/jobs_page.html`) is an Active / History / Pipelines cockpit. History prefers `GET /v1/internal/cuttle/jobs` on the queue server; falls back to local `src/output/cuttle_jobs_history.jsonl`. |

## Safety

- Never uses your normal checked-out Unity branch as the work target.
- Branches are always `cuttle/issue-N` based on `origin/dev/core` (configurable).
- No auto-merge; `dev/cuttle` staging is deferred.
- Bot comments include `<!-- cuttle-bot -->` and are ignored by the webhook.
- Claim leases expire so a crashed worker does not strand jobs forever.
