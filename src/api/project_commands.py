"""
Project-local Cuttle commands: ``{project}/.cuttle/commands/*.md``.

Markdown files with YAML frontmatter (same shape as Cursor skills) appear in the
web chat ``/`` palette when that project is the chat cwd, and expand into agent
prompts (or optional shell runs) when invoked.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

# Built-in web-chat slash heads that project commands must not shadow.
RESERVED_SLASH_NAMES = frozenset({
    "help", "pipelines", "pipeline", "project", "cd",
    "cursor", "cursor-cli", "claude", "hermes", "codex", "muse",
    "opencode", "antigravity", "deepseek",
    "cmd", "commands", "skill", "skills",
    "model", "plan", "ask", "agent", "clear", "sandbox", "about",
})

_FRONTMATTER_SPLIT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _split_frontmatter(raw: str) -> Tuple[Dict[str, Any], str]:
    raw = raw.lstrip("\ufeff")
    m = _FRONTMATTER_SPLIT.match(raw)
    if not m:
        if raw.startswith("---"):
            # Tolerate missing trailing --- by falling back to skills-style split
            end = raw.find("\n---", 3)
            if end == -1:
                return {}, raw
            block = raw[3:end].strip("\n")
            body = raw[end + 4 :].lstrip("\n")
        else:
            return {}, raw
    else:
        block = m.group(1)
        body = raw[m.end() :]

    if yaml is not None:
        try:
            meta = yaml.safe_load(block) or {}
            if not isinstance(meta, dict):
                return {}, body
            return meta, body
        except Exception:
            return {}, body
    meta: Dict[str, Any] = {}
    for line in block.splitlines():
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta, body


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", str(name or "").strip().lower()).strip("-")
    return s or "command"


def _normalize_watch(raw: Any) -> Optional[Dict[str, Any]]:
    """Frontmatter ``watch:`` — id string or dict. Used by execute:shell (no LLM)."""
    if raw is None or raw is False:
        return None
    if isinstance(raw, str):
        ident = raw.strip()
        return {"id": ident} if ident else None
    if not isinstance(raw, dict):
        return None
    ident = str(raw.get("id") or raw.get("job") or "").strip()
    if not ident:
        return None
    out = {"id": ident}
    for key in ("title", "url", "resume_message", "interval_ms"):
        if raw.get(key) is not None and str(raw.get(key)).strip() != "":
            out[key] = raw[key]
    return out


def _commands_dirs_for_project(project_path: str) -> List[Path]:
    """Prefer ``.cuttle/commands`` at the registered root; also check nested git ``source/``."""
    try:
        root = Path(project_path).resolve()
    except OSError:
        return []
    if not root.is_dir():
        return []
    dirs: List[Path] = []
    primary = root / ".cuttle" / "commands"
    if primary.is_dir():
        dirs.append(primary)
    # Unity-style: registered folder is game root, code/git under source/
    nested = root / "source" / ".cuttle" / "commands"
    if nested.is_dir() and nested not in dirs:
        dirs.append(nested)
    return dirs


def _parse_command_file(path: Path, project_path: str) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, body = _split_frontmatter(raw)
    file_stem = path.stem
    name = _slugify(str(meta.get("name") or file_stem))
    if name in RESERVED_SLASH_NAMES:
        # Still expose under /cmd <name> only
        pass
    title = str(meta.get("title") or meta.get("label") or name.replace("-", " ")).strip()
    description = str(
        meta.get("description") or meta.get("desc") or meta.get("hint") or ""
    ).strip()
    aliases_raw = meta.get("aliases") or []
    if isinstance(aliases_raw, str):
        aliases = [_slugify(a) for a in aliases_raw.split(",") if a.strip()]
    elif isinstance(aliases_raw, list):
        aliases = [_slugify(a) for a in aliases_raw if str(a).strip()]
    else:
        aliases = []
    run = meta.get("run") or meta.get("shell") or None
    if run is not None:
        run = str(run).strip() or None
    workdir = str(meta.get("workdir") or meta.get("cwd") or "").strip() or None
    execute = str(meta.get("execute") or meta.get("mode") or "").strip().lower() or None
    if execute not in (None, "shell", "prompt", "agent"):
        execute = None
    if execute == "agent":
        execute = "prompt"
    watch = _normalize_watch(meta.get("watch"))
    timeout = None
    raw_timeout = meta.get("timeout")
    if raw_timeout is not None:
        try:
            timeout = max(5, int(raw_timeout))
        except (TypeError, ValueError):
            timeout = None

    return {
        "name": name,
        "title": title,
        "description": description,
        "aliases": aliases,
        "run": run,
        "workdir": workdir,
        "execute": execute,
        "watch": watch,
        "timeout": timeout,
        "body": body.strip(),
        "path": str(path),
        "project_path": str(Path(project_path).resolve()),
        "reserved_collision": name in RESERVED_SLASH_NAMES,
    }


def list_project_commands(project_path: str) -> List[Dict[str, Any]]:
    """Return palette-ready command dicts for a project path (may be empty)."""
    if not project_path:
        return []
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for commands_dir in _commands_dirs_for_project(project_path):
        try:
            files = sorted(commands_dir.glob("*.md"), key=lambda p: p.name.lower())
        except OSError:
            continue
        for path in files:
            if path.name.upper() == "README.MD":
                continue
            cmd = _parse_command_file(path, project_path)
            if not cmd:
                continue
            key = cmd["name"]
            if key in seen:
                continue
            seen.add(key)
            out.append(cmd)
    out.sort(key=lambda c: (c.get("title") or c.get("name") or "").lower())
    return out


def find_project_command(project_path: str, name: str) -> Optional[Dict[str, Any]]:
    want = _slugify(name)
    if not want:
        return None
    for cmd in list_project_commands(project_path):
        if cmd["name"] == want:
            return cmd
        if want in (cmd.get("aliases") or []):
            return cmd
    return None


def build_agent_prompt(cmd: Dict[str, Any], user_args: str = "") -> str:
    """Expand a project command into the prompt sent to the agent."""
    title = cmd.get("title") or cmd.get("name")
    name = cmd.get("name")
    body = (cmd.get("body") or "").strip()
    project_path = cmd.get("project_path") or ""
    args = (user_args or "").strip()
    parts = [
        f"# Cuttle project command: {title}",
        "",
        f"You are running the **/{name}** project command.",
        f"Project root: `{project_path}`",
        "Follow the instructions below carefully. Prefer tools / shell over narration.",
        "",
    ]
    if body:
        parts.append(body)
        parts.append("")
    else:
        parts.append(
            f"(No markdown body — infer how to run `/{name}` for this project.)"
        )
        parts.append("")
    if args:
        parts.append("## Additional user arguments")
        parts.append(args)
        parts.append("")
    if cmd.get("run"):
        parts.append("## Declared shell recipe (`run`)")
        parts.append("If appropriate, execute this (cwd defaults to project root / workdir):")
        parts.append("```")
        parts.append(str(cmd["run"]))
        parts.append("```")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def resolve_run_cwd(cmd: Dict[str, Any]) -> Path:
    root = Path(cmd.get("project_path") or ".").resolve()
    wd = cmd.get("workdir")
    if not wd:
        return root
    p = Path(wd)
    if not p.is_absolute():
        p = root / p
    return p.resolve()


def _watch_job_running(watch_id: str) -> bool:
    ident = str(watch_id or "").strip()
    if not ident:
        return False
    try:
        from api.job_watch import read_status

        st = read_status(ident) or {}
    except Exception:
        return False
    return str(st.get("state") or "").strip().lower() == "running"


def _drain_pipe(stream, bucket: List[str]) -> None:
    try:
        if stream is None:
            return
        for chunk in iter(stream.readline, ""):
            bucket.append(chunk)
    except Exception:
        pass


def run_project_command_shell(
    cmd: Dict[str, Any],
    user_args: str = "",
    timeout: Optional[int] = None,
    session_id: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute optional frontmatter ``run`` (appends user_args).

    When ``session_id`` is set, the subprocess is registered with the chat run
    registry so Stop / ``/api/chat-cancel`` kills it (and any ``watch:`` job).
    """
    recipe = cmd.get("run")
    if not recipe:
        return {"success": False, "error": "Command has no run: recipe"}
    cwd = resolve_run_cwd(cmd)
    if not cwd.is_dir():
        return {"success": False, "error": f"workdir not found: {cwd}"}
    args = (user_args or "").strip()
    full = f"{recipe} {args}".strip() if args else str(recipe)
    if timeout is None:
        timeout = cmd.get("timeout")
    if timeout is None:
        timeout = 120 if cmd.get("watch") else 3600
    timeout = int(timeout)
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    watch = cmd.get("watch") or {}
    watch_id = str((watch or {}).get("id") or "").strip()

    # One live job → one form. A second /build while the watch is running must
    # not mint another card stamped with the same run_id (CH-000308).
    if watch_id and _watch_job_running(watch_id):
        return {
            "success": False,
            "exit_code": 4,
            "error": (
                "A job is already running on this watch card. "
                "Stop it first, then run again for a new build (new form id)."
            ),
            "output": "",
            "cwd": str(cwd),
            "command": full,
            "reattached": False,
        }

    cancel = None
    if session_id is not None:
        try:
            from api.chat_run_registry import attach_job, begin_run

            cancel = begin_run(session_id)
            if watch_id:
                attach_job(session_id, watch_id)
        except Exception:
            cancel = None

    def _cancelled_result() -> Dict[str, Any]:
        if watch_id:
            try:
                from api.job_watch import cancel_job

                cancel_job(watch_id)
            except Exception:
                pass
        return {
            "success": False,
            "cancelled": True,
            "error": "Cancelled",
            "cwd": str(cwd),
            "command": full,
        }

    def _success_from_watch(output: str, *, reattached: bool = True) -> Dict[str, Any]:
        return {
            "success": True,
            "exit_code": 0,
            "output": output or "(no output)",
            "cwd": str(cwd),
            "command": full,
            # Kick timed out / failed while the watch job is already live —
            # callers must not mint a second progress card for the same run.
            "reattached": bool(reattached),
        }

    proc = None
    out = ""
    err = ""
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []
    try:
        proc = subprocess.Popen(
            full,
            cwd=str(cwd),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        if session_id is not None and proc is not None:
            try:
                from api.chat_run_registry import attach_process

                attach_process(session_id, proc)
            except Exception:
                pass

        readers = [
            threading.Thread(target=_drain_pipe, args=(proc.stdout, stdout_chunks), daemon=True),
            threading.Thread(target=_drain_pipe, args=(proc.stderr, stderr_chunks), daemon=True),
        ]
        for t in readers:
            t.start()

        deadline = time.time() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                try:
                    from api.chat_run_registry import kill_process_tree

                    kill_process_tree(proc)
                except Exception:
                    pass
                return _cancelled_result()
            rc = proc.poll()
            if rc is not None:
                break
            if time.time() >= deadline:
                # Kick scripts spawn a worker then may not close stdio. If the
                # watch job is already running, do not kill it as a "timeout".
                if watch_id and _watch_job_running(watch_id):
                    for t in readers:
                        t.join(timeout=1)
                    out = "".join(stdout_chunks).strip()
                    return _success_from_watch(
                        out or "Kicked — job already running (reattached)."
                    )
                try:
                    from api.chat_run_registry import kill_process_tree

                    kill_process_tree(proc)
                except Exception:
                    pass
                return {
                    "success": False,
                    "error": f"Timed out after {timeout}s",
                    "cwd": str(cwd),
                    "command": full,
                }
            time.sleep(0.15)

        for t in readers:
            t.join(timeout=2)
        out = "".join(stdout_chunks)
        err = "".join(stderr_chunks)
        if cancel is not None and cancel.is_set():
            return _cancelled_result()
        if proc.returncode != 0 and watch_id and _watch_job_running(watch_id):
            return _success_from_watch(
                (out or "").strip() or "Kicked — job already running (reattached)."
            )
    except Exception as e:
        if watch_id and _watch_job_running(watch_id):
            return _success_from_watch(
                f"Kicked — job already running (reattached). ({e})"
            )
        if proc is not None:
            try:
                from api.chat_run_registry import kill_process_tree

                kill_process_tree(proc)
            except Exception:
                pass
        return {"success": False, "error": str(e), "cwd": str(cwd), "command": full}
    finally:
        if session_id is not None:
            try:
                from api.chat_run_registry import end_run

                end_run(session_id, proc=proc)
            except Exception:
                pass

    combined_out = (out or "").strip()
    combined_err = (err or "").strip()
    combined = combined_out
    if combined_err:
        combined = (combined + ("\n\n" if combined else "") + combined_err).strip()
    return {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": combined or "(no output)",
        "cwd": str(cwd),
        "command": full,
    }


def _watch_form_spec_for_command(cmd: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    watch = cmd.get("watch") or {}
    ident = str((watch or {}).get("id") or "").strip()
    if not ident:
        return None
    from api.job_watch import watch_form_spec

    title = str(watch.get("title") or cmd.get("title") or cmd.get("name") or "Job")
    spec = watch_form_spec(ident, title=title)
    if watch.get("url"):
        spec["watch"]["url"] = str(watch["url"]).strip()
    if watch.get("interval_ms") is not None:
        try:
            spec["watch"]["interval_ms"] = max(1500, int(watch["interval_ms"]))
        except (TypeError, ValueError):
            pass
    if watch.get("resume_message"):
        spec["watch"]["resume_message"] = str(watch["resume_message"]).strip()
    try:
        from api.job_watch import read_status_reconciled, stamp_watch_run, watch_snapshot_from_status

        status = read_status_reconciled(ident, persist=False)
        spec["watch"] = stamp_watch_run(spec["watch"], status)
        spec["watch"]["snapshot"] = watch_snapshot_from_status(status)
    except Exception:
        pass
    return spec


_ALREADY_RUNNING_RE = re.compile(
    # Require the explicit "already running" job phrases — do not match the
    # timeout-path note "Kicked — job already running (reattached)." alone via
    # a bare "reattached", or a legitimate first kick would lose its form.
    r"(?:a\s+(?:release\s+)?job is already running)"
    r"|(?:reattach(?:ed)?\s+the\s+watch\s+form)"
    r"|(?:already running on this watch card)",
    re.IGNORECASE,
)


def _should_omit_watch_form(result: Dict[str, Any]) -> bool:
    """True when this invocation did not start a *new* build/run."""
    if int(result.get("exit_code") or 0) == 4:
        return True
    blob = "\n".join(
        [
            str(result.get("error") or "").strip(),
            str(result.get("output") or "").strip(),
        ]
    )
    return bool(blob and _ALREADY_RUNNING_RE.search(blob))


def format_project_command_shell_reply(cmd: Dict[str, Any], result: Dict[str, Any]) -> str:
    """Assistant HTML/markdown for a programmatic command. May include a watch form tag."""
    import json

    name = cmd.get("name") or "command"
    title = cmd.get("title") or name
    if result.get("cancelled"):
        return f"**/{name}** cancelled."
    output = (result.get("output") or "").strip()
    ok = bool(result.get("success"))
    # Never mint a second card for the same live run_id — each successful
    # attempt must own its own form id + build run id.
    if cmd.get("watch") and _should_omit_watch_form(result):
        return (
            f"**/{name}** not started — a build is already running.\n\n"
            "Use **Stop job** on that watch card, then run again. "
            "Each attempt gets its own build and form."
        )
    if ok and cmd.get("watch"):
        spec = _watch_form_spec_for_command(cmd)
        if spec:
            form = json.dumps(spec, ensure_ascii=False, indent=2)
            # Kick stdout (1% / Poll /output/…) belongs on the card, not
            # as a transcript dump under it.
            return (
                f"**/{name}** started.\n\n"
                f"<cuttle_action_form>\n{form}\n</cuttle_action_form>"
            )
    if ok:
        return f"**/{name}** (`{title}`) finished.\n\n```\n{output}\n```"
    code = result.get("exit_code")
    err = (result.get("error") or "").strip()
    extra = f" (exit {code})" if code is not None else ""
    bits = [f"**/{name}** failed{extra}."]
    if err:
        bits.append(err)
    if output:
        bits.append(f"```\n{output}\n```")
    return "\n\n".join(bits)


_STICKY_HEAD_RE = re.compile(
    r"^(?P<head>/(?:cursor(?:-cli)?|claude|hermes|codex|muse|opencode|antigravity|deepseek))\s+",
    re.IGNORECASE,
)


def try_expand_message_project_command(
    message: str,
    project_path: Optional[str],
) -> Optional[Dict[str, Any]]:
    """
    If ``message`` invokes a project command (optionally after a sticky agent
    prefix), return expansion metadata.

    Returns dict:
      action: 'prompt' | 'shell'
      message: rewritten chat message (sticky prefix preserved for prompt path)
      command: command dict
      args: trailing args
    """
    if not message or not project_path:
        return None
    raw = message.strip()
    sticky = ""
    rest = raw
    m = _STICKY_HEAD_RE.match(raw)
    if m:
        sticky = m.group("head").lower()
        # normalize to canonical form with trailing space for rewrite
        sticky = "/" + sticky.lstrip("/").split()[0].lower() + " "
        rest = raw[m.end() :].lstrip()

    # /cmd <name> [args...]
    cmd_m = re.match(r"^/cmd(?:s|ommand)?\s+(\S+)(?:\s+(.*))?$", rest, flags=re.I | re.S)
    name = None
    args = ""
    if cmd_m:
        name = cmd_m.group(1)
        args = (cmd_m.group(2) or "").strip()
    else:
        # /build-deploy [args]  or  /build [args]
        slash_m = re.match(r"^/([a-zA-Z][\w.-]*)(?:\s+(.*))?$", rest, flags=re.S)
        if not slash_m:
            return None
        name = slash_m.group(1)
        args = (slash_m.group(2) or "").strip()
        if _slugify(name) in RESERVED_SLASH_NAMES:
            return None

    cmd = find_project_command(project_path, name)
    if not cmd:
        return None

    # execute: shell | prompt — default: shell when run-only; else prompt for the agent.
    execute = cmd.get("execute")
    if not execute:
        if cmd.get("run") and not (cmd.get("body") or "").strip():
            execute = "shell"
        else:
            execute = "prompt"

    if execute == "shell" and cmd.get("run"):
        return {
            "action": "shell",
            "command": cmd,
            "args": args,
            "sticky": sticky.strip(),
            "message": raw,
        }

    prompt = build_agent_prompt(cmd, args)
    if sticky:
        # Keep sticky agent; prompt becomes the CLI / agent argument body.
        new_message = sticky + prompt
    else:
        new_message = prompt
    return {
        "action": "prompt",
        "command": cmd,
        "args": args,
        "sticky": sticky.strip(),
        "message": new_message,
        "prompt": prompt,
    }
