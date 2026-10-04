# Gitea issues (Cuttle global — all projects)

Native runbook for **reading** and **updating** issues on a Gitea server.
Per-project repo aliases live in `{project}/.cuttle/actions/gitea-issue.yaml`.

**Use the REST API** — not browser automation. Gitea exposes issue, label, and
comment endpoints under `/api/v1`.

Install-local base URL / example repos may live in `.cuttle_global/personal/docs/gitea.md`.

## When the user mentions Gitea / bug reports / issues

1. Read **this file**. Prefer the personal overlay if present.
2. Resolve the **active project** path (chat chip / `project_path` / workspace).
3. Open `{project}/.cuttle/actions/gitea-issue.yaml` for repo aliases.
4. Optional: `{project}/.cuttle/docs/gitea.md` for project label conventions.

## Auth (do not commit)

**Preferred:** API token (scoped) — not the account password.

1. Gitea → **Settings → Applications → Generate New Token**
2. Scopes: at least **`issue`** and **`repository`** (read). Add write scopes for
   comments/labels if your Gitea build separates them.
3. Put credentials in **`{CuttleInstall}/src/.env`**:

```env
GITEA_BASE_URL=http://<gitea-host>:<port>
GITEA_TOKEN=your_token_here
```

**Fallback** (basic auth — works but less ideal):

```env
GITEA_BASE_URL=http://<gitea-host>:<port>
GITEA_USERNAME=cuttle
GITEA_PASSWORD=your_password_here
```

Restart Flask after changing `.env`.

## Read issues (agent / shell — no confirm)

From the Cuttle tree:

```bash
# From the Cuttle repository root
PYTHONPATH=src .venv/bin/python -m api.gitea list "<owner>/<repo>" --state open --json
PYTHONPATH=src .venv/bin/python -m api.gitea show "<owner>/<repo>" 12 --json
PYTHONPATH=src .venv/bin/python -m api.gitea comments "<owner>/<repo>" 12 --json
PYTHONPATH=src .venv/bin/python -m api.gitea labels "<owner>/<repo>" --json
```

(Legacy shim: `.cuttle_global\scripts\gitea_cli.py` — prefer `python -m api.gitea`.)
Repo aliases come from the active project's `gitea-issue.yaml`.

## Post comment / set labels

**Prefer direct CLI updates** when the project runbook says so.

Confirm-gated fallback:

```text
<cuttle_confirm action="gitea.issue" repo="<alias-or-owner/repo>" issue="12" labels_add="Status/In-Progress">
Starting work on this.
</cuttle_confirm>
```

Params:

| Param | Meaning |
|---|---|
| `repo` | Alias from `gitea-issue.yaml` **or** `owner/repo` |
| `issue` | Issue number (Gitea `index`) |
| Body text | Comment body (confirm inner text) |
| `labels` | Replace all labels on the issue |
| `labels_add` | Add labels (comma-separated or JSON list) |
| `labels_remove` | Remove labels by name |

**Label names are per-project** — read `{project}/.cuttle/docs/gitea.md`. Do not invent
bare names like `in-progress` / `done` unless that repo has them.

Assignee defaults to the `cuttle` Gitea user (`GITEA_AGENT_USERNAME` in `src/.env` overrides).

## API reference (common endpoints)

| Task | Method | Path |
|---|---|---|
| List issues | GET | `/api/v1/repos/{owner}/{repo}/issues?state=open` |
| Get issue | GET | `/api/v1/repos/{owner}/{repo}/issues/{index}` |
| List comments | GET | `/api/v1/repos/{owner}/{repo}/issues/{index}/comments` |
| Add comment | POST | `/api/v1/repos/{owner}/{repo}/issues/{index}/comments` |
| Add labels | POST | `/api/v1/repos/{owner}/{repo}/issues/{index}/labels` |
| Replace labels | PUT | `/api/v1/repos/{owner}/{repo}/issues/{index}/labels` |
| Assign / close | PATCH | `/api/v1/repos/{owner}/{repo}/issues/{index}` (`assignees`, `state`) |
| Repo labels | GET | `/api/v1/repos/{owner}/{repo}/labels` |

Auth header: `Authorization: token <GITEA_TOKEN>` (note the word `token`).

Official docs: https://docs.gitea.com/development/api-usage/

## Per-project setup

- `{project}/.cuttle/actions/gitea-issue.yaml` — `repos:` aliases + `type: gitea.issue`
- `{project}/.cuttle/docs/gitea.md` — label names your team uses

## Polling / auto-triage (optional)

Remote `@cuttle` commands may be handled by the **Cuttle Jobs** stack. See
`cuttle-jobs.md` and install-local notes under `.cuttle_global/personal/docs/` when present.

## Shell environment

Examples run from the Cuttle repository root with the project venv. POSIX
examples set `PYTHONPATH=src` per invocation. For Windows PowerShell, set
the path and use the Windows interpreter; for example:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m api.gitea list "<owner>/<repo>" --state open --json
```

Use PowerShell backticks for multiline continuation and single-quoted JSON
arguments; POSIX examples use backslashes for continuation.

## Project guidance opt-in

Gitea runtime integrations and Jobs remain supported. To advertise this global
runbook in a project's context inventory, ranker, and capability summary, set
`[integrations] gitea = on` in that project's `.cuttle/GLOBAL.ini` (or its
install-local personal twin). A default localhost URL/agent username alone does
not enable guidance. Set up credentials separately using the instructions above.
Project-owned forge docs remain project-owned even when global guidance is off.
