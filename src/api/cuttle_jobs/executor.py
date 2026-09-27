"""Execute claimed Gitea ``@cuttle`` jobs (investigate / solve)."""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from api import gitea_client
from api.cuttle_jobs.client import CuttleJobsClient, load_jobs_config
from api.cuttle_jobs.commands import with_bot_marker
from api.cuttle_jobs.formatting import (
    channel_constraints,
    format_ack_comment,
    format_converse_comment,
    format_decline_comment,
    format_investigate_comment,
    format_solve_comment,
)
from api.cuttle_jobs.workspace import (
    WorkspaceError,
    commit_all,
    ensure_clean,
    git_workdir,
    list_changed_files,
    park_workspace,
    prepare_issue_branch,
    push_branch,
    reset_hard_clean,
    resolve_workspace,
    suggest_job_commit_message,
)

log = logging.getLogger("cuttle.jobs")

NEEDS_TESTING_LABEL = "Status/Needs Testing"
IN_PROGRESS_LABEL = "Status/In-Progress"

# Heartbeat while the agent is blocked in-process. Lease default is 1800s;
# renewing every ~90s keeps a long Cursor turn from being reclaimed.
_HEARTBEAT_INTERVAL_SEC = 90.0


class AgentJobError(RuntimeError):
    """Agent ran but failed to produce a usable result (not infra)."""


def _parse_owner_repo(repository: str) -> tuple[str, str]:
    return gitea_client.parse_owner_repo(repository)


def _post_comment(owner: str, repo: str, issue: int, body: str) -> None:
    gitea_client.add_issue_comment(owner, repo, issue, with_bot_marker(body))


def _set_labels_transition(
    owner: str,
    repo: str,
    issue: int,
    *,
    add: list[str],
    remove_names: list[str],
) -> None:
    try:
        labels = gitea_client.list_repo_labels(owner, repo)
        by_name = {str(l.get("name")): l for l in labels}
        for name in remove_names:
            lab = by_name.get(name)
            if lab and lab.get("id") is not None:
                try:
                    gitea_client.delete_issue_label(owner, repo, issue, int(lab["id"]))
                except Exception as e:
                    log.warning("remove label %s failed: %s", name, e)
        to_add = [n for n in add if n in by_name]
        if to_add:
            gitea_client.add_issue_labels(owner, repo, issue, to_add)
    except Exception as e:
        log.warning("label transition failed: %s", e)


def _issue_context_block(job: Dict[str, Any], *, thread: str = "") -> str:
    title = job.get("issue_title") or ""
    body = job.get("issue_body") or ""
    instructions = job.get("instructions") or ""
    comment_body = job.get("comment_body") or ""
    lines = [
        f"Repository: {job.get('repository')}",
        f"Issue: #{job.get('issue_number')} — {title}",
        "",
        "## Issue body",
        body or "(empty)",
        "",
    ]
    if thread:
        lines.extend(["## Recent issue thread", thread, ""])
    if comment_body:
        lines.extend(["## Triggering comment", comment_body, ""])
    if instructions:
        lines.extend(["## Extra instructions from the triggering comment", instructions, ""])
    return "\n".join(lines)


def _format_issue_thread(comments: list, *, limit: int = 12) -> str:
    if not comments:
        return "(no comments yet)"
    chunks: list[str] = []
    for c in comments[-limit:]:
        user = ((c.get("user") or {}).get("login")) or "?"
        body = str(c.get("body") or "").strip()
        for marker in ("<!-- cuttle-bot -->", "<!-- cuttle-offer:implement -->"):
            if marker in body:
                body = body.replace(marker, "").strip()
        if len(body) > 2500:
            body = body[:2500] + "\n…(truncated)"
        chunks.append(f"**@{user}:**\n{body}")
    return "\n\n---\n\n".join(chunks)


def _fetch_thread(owner: str, repo: str, issue: int) -> str:
    try:
        comments = gitea_client.list_issue_comments(owner, repo, issue)
        return _format_issue_thread(comments)
    except Exception as e:
        log.warning("list_issue_comments failed: %s", e)
        return "(could not load comments)"


def _build_investigate_prompt(job: Dict[str, Any], *, thread: str = "") -> str:
    return (
        "You are Cuttle running a **read-only** investigation for a Gitea issue.\n"
        "Do NOT modify code, commit, push, or change branches.\n"
        "Read the issue, the thread below, and inspect the codebase.\n\n"
        f"{channel_constraints()}\n"
        "Reply with a **compact** markdown report (emoji section headers OK), covering:\n"
        "- 🎯 Likely root cause (2–4 sentences)\n"
        "- 📂 Relevant files/code (short table or bullets)\n"
        "- 💡 Suggested fix\n"
        "- ❓ Uncertainties (only if real)\n"
        "- 🧪 Manual testing / clarification needed\n\n"
        "Closing offer (important):\n"
        "- If you have **high confidence** you can implement a focused fix with the "
        "context you already have, end your report with this exact line:\n"
        "  Would you like me to implement the change/fix?\n"
        "- If confidence is low or you need more info, ask clarifying questions instead "
        "and do **not** use that phrase.\n"
        "- Remind the human they can reply with `@cuttle yes` to implement, or "
        "`@cuttle …` to keep talking.\n\n"
        + _issue_context_block(job, thread=thread)
    )


def _build_converse_prompt(job: Dict[str, Any], *, thread: str = "") -> str:
    return (
        "You are Cuttle continuing a **conversation** on a Gitea issue thread.\n"
        "Do NOT modify code, commit, push, or change branches in this turn.\n"
        "Answer the human's latest message helpfully and concretely — keep it short.\n\n"
        f"{channel_constraints()}\n"
        "If they are asking you to implement a fix and you have enough confidence, "
        "tell them to reply `@cuttle yes` or `@cuttle solve` (you will not start coding "
        "in this converse turn).\n"
        "If they are asking for more analysis, investigate verbally and offer the same "
        "closing line when high-confidence:\n"
        "  Would you like me to implement the change/fix?\n\n"
        + _issue_context_block(job, thread=thread)
    )


def _build_solve_prompt(job: Dict[str, Any], branch: str, base: str, *, thread: str = "") -> str:
    return (
        "You are Cuttle implementing a fix for a Gitea issue in an **isolated** git workspace.\n"
        f"You are already on branch `{branch}` (based on `{base}`).\n"
        "Implement a focused fix for this issue only.\n"
        "Do NOT merge, force-push, or touch unrelated branches (especially human branches).\n"
        "Do NOT use `dev/cuttle` unless explicitly asked in a future stage command.\n"
        "Leave your code changes uncommitted if the orchestrator will commit — "
        "prefer making the code edits; the worker commits after you finish.\n\n"
        f"{channel_constraints()}\n"
        "When done, write a **compact** Gitea comment body (emoji headers OK):\n"
        f"- Title like `## ✅ Fix ready · #{job.get('issue_number')}`\n"
        "- 🎯 What changed / why (few sentences)\n"
        "- 🧪 What still needs manual playtest\n"
        "- Do **not** list every file path (the worker appends a short file footer + PR links).\n"
        "- Do **not** post Discord forms or update the Gitea issue yourself.\n\n"
        + _issue_context_block(job, thread=thread)
    )


def _run_agent(prompt: str, project_path: str, timeout: float) -> Dict[str, Any]:
    cfg = load_jobs_config()
    agent = cfg.get("agent") or "cursor"
    from api.agent_harness.kernel import run_agent_web_command

    return run_agent_web_command(
        agent,
        prompt,
        None,
        project_path=project_path,
        timeout=timeout,
    )


def _heartbeat_loop(
    client: CuttleJobsClient,
    job_id: int,
    stop: threading.Event,
    interval: float = _HEARTBEAT_INTERVAL_SEC,
) -> None:
    """Renew the claim lease for the whole agent turn (runs concurrently)."""
    # Immediate renew so a slow first minute still extends past the original lease window
    # if the claim was already aged (reclaimed job).
    while True:
        try:
            client.heartbeat(job_id)
            log.info("heartbeat ok job=%s", job_id)
        except Exception as e:
            log.warning("heartbeat failed job=%s: %s", job_id, e)
        if stop.wait(interval):
            return


def _format_infra_failure(exc: BaseException, *, retry: bool, attempt: int) -> str:
    code = getattr(exc, "code", None) or type(exc).__name__
    retry_line = (
        f"This looks **retryable** — Cuttle will try again when available (attempt {attempt})."
        if retry
        else "This was marked **permanent** — fix the infra issue, then re-comment `@cuttle …`."
    )
    return (
        "## Cuttle infrastructure failure\n\n"
        f"**Code:** `{code}`\n\n"
        f"{exc}\n\n"
        f"{retry_line}\n"
    )


def _format_agent_failure(exc: BaseException, *, retry: bool, attempt: int) -> str:
    retry_line = (
        f"Cuttle may retry automatically (attempt {attempt})."
        if retry
        else "Marked permanent — re-comment `@cuttle …` after adjusting the issue/instructions."
    )
    return (
        "## Cuttle agent failure\n\n"
        f"{exc}\n\n"
        f"{retry_line}\n"
    )


def execute_job(
    job: Dict[str, Any],
    *,
    client: Optional[CuttleJobsClient] = None,
) -> Dict[str, Any]:
    """Run one claimed job. Raises on failure (caller decides retry)."""
    client = client or CuttleJobsClient()
    job_id = int(job["id"])
    repository = str(job["repository"])
    issue_number = int(job["issue_number"])
    command = str(job["command"]).lower().strip()
    target_branch = str(job.get("target_branch") or "dev/core").strip() or "dev/core"
    owner, repo = _parse_owner_repo(repository)
    workdir = None

    log.info(
        "execution started job=%s cmd=%s repo=%s issue=%s",
        job_id,
        command,
        repository,
        issue_number,
    )

    stop_hb = threading.Event()
    hb = threading.Thread(
        target=_heartbeat_loop,
        args=(client, job_id, stop_hb),
        daemon=True,
        name=f"cuttle-job-hb-{job_id}",
    )
    hb.start()
    log.info(
        "heartbeat thread started job=%s interval=%ss (runs during agent turn)",
        job_id,
        int(_HEARTBEAT_INTERVAL_SEC),
    )

    try:
        try:
            gitea_client.patch_issue(
                owner, repo, issue_number, assignees=[gitea_client.default_agent_username()]
            )
            _set_labels_transition(
                owner,
                repo,
                issue_number,
                add=[IN_PROGRESS_LABEL],
                remove_names=[],
            )
            # Deliberately NOT an @cuttle command — recursion guards also cover bot user + marker.
            # Decline gets a single stylized reply (no "working on it" ack).
            if command != "decline":
                _post_comment(
                    owner,
                    repo,
                    issue_number,
                    format_ack_comment(command, job.get("triggering_user"), job_id),
                )
        except Exception as e:
            log.warning("start-of-job gitea update failed: %s", e)

        if command == "decline":
            _post_comment(
                owner,
                repo,
                issue_number,
                format_decline_comment(),
            )
            _set_labels_transition(
                owner,
                repo,
                issue_number,
                add=[],
                remove_names=[IN_PROGRESS_LABEL],
            )
            log.info("job completed (decline) id=%s", job_id)
            return {"command": "decline", "ok": True}

        workspace = resolve_workspace(repository)
        workdir = git_workdir(workspace)
        cfg = load_jobs_config()
        timeout = float(cfg.get("timeout") or 3600)
        thread = _fetch_thread(owner, repo, issue_number)

        if command == "converse":
            ensure_clean(workdir)
            result = _run_agent(
                _build_converse_prompt(job, thread=thread),
                str(workspace),
                timeout,
            )
            if not (result or {}).get("success", True) and (result or {}).get("type", "").endswith(
                "_error"
            ):
                raise AgentJobError(
                    str((result or {}).get("response") or "converse agent returned an error")[:1500]
                )
            try:
                ensure_clean(workdir)
            except WorkspaceError:
                log.warning("converse left dirty workspace; resetting")
                reset_hard_clean(workdir)

            response = str((result or {}).get("response") or "").strip()
            _post_comment(
                owner, repo, issue_number, format_converse_comment(response)
            )
            _set_labels_transition(
                owner,
                repo,
                issue_number,
                add=[],
                remove_names=[IN_PROGRESS_LABEL],
            )
            try:
                park_workspace(workdir, base_branch=target_branch)
            except Exception as e:
                log.warning("park after converse failed: %s", e)
            log.info("job completed (converse) id=%s", job_id)
            return {"command": "converse", "ok": True}

        if command == "investigate":
            ensure_clean(workdir)
            result = _run_agent(
                _build_investigate_prompt(job, thread=thread),
                str(workspace),
                timeout,
            )
            if not (result or {}).get("success", True) and (result or {}).get("type", "").endswith(
                "_error"
            ):
                raise AgentJobError(
                    str((result or {}).get("response") or "investigate agent returned an error")[:1500]
                )

            try:
                ensure_clean(workdir)
            except WorkspaceError:
                log.warning("investigate left dirty workspace; resetting")
                reset_hard_clean(workdir)

            response = str((result or {}).get("response") or "").strip()
            body = format_investigate_comment(response, issue_number=issue_number)
            _post_comment(owner, repo, issue_number, body)
            _set_labels_transition(
                owner,
                repo,
                issue_number,
                add=[],
                remove_names=[IN_PROGRESS_LABEL],
            )
            try:
                park_workspace(workdir, base_branch=target_branch)
            except Exception as e:
                log.warning("park after investigate failed: %s", e)
            log.info("job completed (investigate) id=%s", job_id)
            return {"command": "investigate", "ok": True}

        if command == "solve":
            branch, created = prepare_issue_branch(
                workdir,
                issue_number=issue_number,
                base_branch=target_branch,
            )
            log.info(
                "branch %s for issue %s (%s from origin/%s when new)",
                branch,
                issue_number,
                "created" if created else "reused",
                target_branch,
            )

            result = _run_agent(
                _build_solve_prompt(job, branch, target_branch, thread=thread),
                str(workspace),
                timeout,
            )
            agent_text = str((result or {}).get("response") or "").strip()
            if not (result or {}).get("success", True) and (result or {}).get("type", "").endswith(
                "_error"
            ):
                raise AgentJobError(agent_text or "solve agent returned an error")

            commit_msg = suggest_job_commit_message(
                workdir,
                issue_number=issue_number,
                issue_title=str(job.get("issue_title") or ""),
                triggering_user=str(job.get("triggering_user") or ""),
            )
            sha = commit_all(workdir, commit_msg)
            pr = None
            pr_url = None
            commit_url = None
            if sha:
                log.info("commit created %s", sha)
                push_branch(workdir, branch)
                log.info("branch pushed %s (remote kept for follow-ups)", branch)
                commit_url = gitea_client.commit_web_url(owner, repo, sha)
                pr = gitea_client.find_open_pull_for_head(owner, repo, branch)
                if pr is None:
                    title = job.get("issue_title") or f"Fix #{issue_number}"
                    pr = gitea_client.create_pull(
                        owner,
                        repo,
                        title=f"[Cuttle] {title}"[:200],
                        body=(
                            f"Automated fix for #{issue_number}.\n\n"
                            f"Branch: `{branch}`\n"
                            f"Base: `{target_branch}`\n\n"
                            "Do **not** auto-merge — human testing required.\n"
                        ),
                        head=branch,
                        base=target_branch,
                    )
                    log.info("PR created #%s", pr.get("number") or pr.get("index"))
                else:
                    log.info("PR reused #%s", pr.get("number") or pr.get("index"))
                pr_index = int(pr.get("number") or pr.get("index") or 0)
                if pr_index:
                    pr_url = gitea_client.pull_web_url(owner, repo, pr_index)
            else:
                log.info("no code changes to commit for job %s", job_id)

            changes = []
            if sha:
                changes = list_changed_files(workdir, f"origin/{target_branch}")

            body = format_solve_comment(
                agent_text,
                issue_number=issue_number,
                changes=changes,
                pr_url=pr_url,
                commit_url=commit_url,
            )

            _post_comment(owner, repo, issue_number, body)
            _set_labels_transition(
                owner,
                repo,
                issue_number,
                add=[NEEDS_TESTING_LABEL],
                remove_names=[IN_PROGRESS_LABEL],
            )
            try:
                # Park on origin/dev/core detached — does not delete cuttle/issue-N remotes.
                park_workspace(workdir, base_branch=target_branch)
            except Exception as e:
                log.warning("park after solve failed: %s", e)
            log.info("Gitea comment posted; job completed (solve) id=%s", job_id)
            return {
                "command": "solve",
                "ok": True,
                "branch": branch,
                "sha": sha,
                "pr_url": pr_url,
            }

        raise WorkspaceError(f"Unsupported command: {command}", code="unsupported_command")
    finally:
        stop_hb.set()


def process_claimed_job(job: Dict[str, Any], client: Optional[CuttleJobsClient] = None) -> None:
    """Execute and ACK complete/fail. Never raises to the poll loop."""
    from api.cuttle_jobs.status_store import (
        append_history,
        get_running,
        mark_done,
        mark_running,
        notify_tray,
    )
    from datetime import datetime, timezone

    client = client or CuttleJobsClient()
    job_id = int(job["id"])
    attempt = int(job.get("attempt_count") or 1)
    command = str(job.get("command") or "").lower().strip()
    repo = str(job.get("repository") or "")
    issue = job.get("issue_number")
    label = f"{command} {repo}#{issue}"

    mark_running(job)
    notify_tray(f"Started {label}", variant="info")

    outcome_status = "failed"
    outcome_error: Optional[str] = None
    outcome_result: Optional[Dict[str, Any]] = None

    def _notify(body: str) -> None:
        try:
            owner, repo_name = _parse_owner_repo(str(job["repository"]))
            _post_comment(owner, repo_name, int(job["issue_number"]), body)
            _set_labels_transition(
                owner,
                repo_name,
                int(job["issue_number"]),
                add=[],
                remove_names=[IN_PROGRESS_LABEL],
            )
        except Exception as notify_err:
            log.warning("failure comment failed: %s", notify_err)

    try:
        result = execute_job(job, client=client)
        client.complete(job_id, result=result)
        log.info("job completed id=%s", job_id)
        notify_tray(f"Finished {label}", variant="success")
        outcome_status = "completed"
        outcome_result = result if isinstance(result, dict) else {"ok": True}
    except WorkspaceError as e:
        log.error("job failed (infra, permanent) id=%s code=%s: %s", job_id, getattr(e, "code", ""), e)
        _notify(_format_infra_failure(e, retry=False, attempt=attempt))
        notify_tray(f"Failed {label}: {e}", variant="error")
        outcome_error = f"infra:{getattr(e, 'code', 'workspace')}:{e}"
        try:
            client.fail(job_id, error=outcome_error, retry=False)
        except Exception as ack_err:
            log.error("fail ack failed: %s", ack_err)
    except AgentJobError as e:
        log.error("job failed (agent) id=%s: %s", job_id, e)
        # Agent failures are usually worth one human look; do not infinite-loop.
        _notify(_format_agent_failure(e, retry=False, attempt=attempt))
        notify_tray(f"Agent failed {label}: {e}", variant="error")
        outcome_error = f"agent:{e}"
        try:
            client.fail(job_id, error=outcome_error, retry=False)
        except Exception as ack_err:
            log.error("fail ack failed: %s", ack_err)
    except Exception as e:
        log.exception("job failed (infra, retryable) id=%s: %s", job_id, e)
        _notify(_format_infra_failure(e, retry=True, attempt=attempt))
        notify_tray(f"Error {label}: {e}", variant="error")
        outcome_error = f"infra_retry:{e}"
        outcome_status = "pending"  # re-queued for retry
        try:
            client.fail(job_id, error=outcome_error, retry=True)
        except Exception as ack_err:
            log.error("fail ack failed: %s", ack_err)
    finally:
        running = get_running(job_id) or {}
        start_time = str(running.get("start_time") or job.get("claimed_at") or "")
        end_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        duration_sec = None
        if start_time:
            try:
                start_dt = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                duration_sec = max(0, int((datetime.now(timezone.utc) - start_dt).total_seconds()))
            except ValueError:
                duration_sec = None
        try:
            append_history(
                {
                    "job_id": job_id,
                    "command": command,
                    "repository": repo,
                    "issue_number": issue,
                    "issue_title": job.get("issue_title") or running.get("issue_title") or "",
                    "gitea_url": running.get("gitea_url") or "",
                    "triggering_user": job.get("triggering_user") or running.get("triggering_user") or "",
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_sec": duration_sec,
                    "status": outcome_status,
                    "error": outcome_error,
                    "result": outcome_result,
                }
            )
        except Exception as hist_err:
            log.warning("append_history failed: %s", hist_err)
        mark_done(job_id)
