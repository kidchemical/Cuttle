"""Idempotent `{project}/.cuttle/` scaffold for every Cuttle-registered project.

Called from ``ProjectManager.add_*`` after a successful DB insert, and available
as a standalone helper when an agent creates a project by hand (no slash command).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Union

CUTTLE_SUBDIRS = (
    "commands",
    "rules",
    "actions",
    "docs",
    "scripts",
    "memory",
    "agents",
    "skills",
)

# Project-root scratch dir (gitignored). Created alongside `.cuttle/` so agents
# have a canonical dump location on day one.
PROJECT_TEMP_DIR = "temp"
_TEMP_GITIGNORE_LINE = "/temp/"

_README = """# `.cuttle/` — per-project Cuttle config

Mirror of the Cuttle global layout. This project owns its own commands, rules,
actions, and docs — it does **not** inherit another project's recipes.

```text
.cuttle/
  commands/*.md     # slash commands (/name)
  rules/*.md        # always-on (Context Compiler / Cuttle Brain)
  actions/*.yaml    # allowlisted side effects (forms / confirms)
  docs/             # runbooks
  scripts/          # optional shell helpers
  skills/<id>/SKILL.md # optional project skills
  agents/<id>/      # optional harness agents
  memory/           # reserved
```

Also created at the project root (not under `.cuttle/`):

```text
temp/               # agent scratch / redirected stdout (gitignored)
```

Install-local overlay (gitignored except README):

```text
.cuttle/personal/   # mirrors commands|rules|actions|docs|scripts|skills — rules/docs append; structured units replace
```

Global reference: see this repo's `.cuttle_global/README.md` and `.cuttle_global/docs/commands-and-actions.md`
(relative to your Cuttle install — do not hardcode another machine's drive path).
"""

_COMMANDS_README = """# Project commands

Drop one markdown file per slash command (YAML frontmatter + body).

See global: `.cuttle_global/docs/commands-and-actions.md` under your Cuttle install.
"""

_RULES_CORE = """# {name} — always-on project rules

These files under `.cuttle/rules/` are compiled into every harness agent turn by the
**Context Compiler** (Cuttle Brain). Keep them short and agent-agnostic.

## Project layout

| Path | Role |
|---|---|
| (fill in) | Primary source / content root |
| `temp/` | Agent scratch / redirected stdout (gitignored; global `00-core.md` scratch rule) |
| `.cuttle/personal/` | Install-local overlay (gitignored; supplements tracked `.cuttle/`) |
| `.cuttle/commands/` | Cuttle slash commands (`/name`) |
| `.cuttle/actions/` | Allowlisted side effects (forms / confirms) |
| `.cuttle/docs/` | Runbooks and design notes |

## Hard rules

1. Prefer this project's `.cuttle/commands`, `.cuttle/actions`, and `.cuttle/docs` over inventing parallel conventions.
2. Long OS jobs should use project commands with `execute: shell` + `watch:` when available.
3. Scratch / `_tmp_*` dumps go in `temp/` (or `scripts/temp/` if this project keeps agent scripts under `scripts/`) — never next to kept helpers or at the repo root. See the scratch rule in global `.cuttle_global/rules/00-core.md`.
4. Machine-specific paths / LAN notes → `.cuttle/personal/` (never commit).
"""

_PERSONAL_README = """# `.cuttle/personal/` — install-local overlay

Gitignored twin of tracked `.cuttle/`. Rule/doc markdown appends a personal delta
after tracked text. Commands/actions replace by declared name; skills replace by
directory id. Personal-only units are supported. A structured unit with
`disabled: true` hides lower-priority units of the same identity. Put LAN hosts, absolute paths, and guild/repo examples here — not
in tracked docs. See the Cuttle global `.cuttle_global/personal/README.md` for the full contract.
"""

_GLOBAL_INI = """# `.cuttle/GLOBAL.ini` — per-project routing contract: which global layers apply here.
# Absent file = everything on and additive. Unknown keys/values fall back to defaults.
# Safety core (`.cuttle_global/rules/00-safety.md`) always compiles and cannot be
# severed per project; edit the global tree to change it for everyone.
[global]
# rules: append (global + project compile) | shadow (a same-basename project file
#   replaces the global one — use to retract a global rule for this project) | off
rules = append
# docs: on | off (skip the global docs inventory leg for this project)
docs = on
# actions: on | off (skip the global actions fallback leg for this project)
actions = on
# skills/commands: on | off (global discovery, including global personal)
skills = on
commands = on
# Optional integration guidance (default off; no service startup):
# [integrations]
# gitea = on
"""


_TEMP_GITIGNORE_LINE = "/temp/"
_PERSONAL_GITIGNORE_LINE = ".cuttle/personal/"


def _ensure_gitignore_line(
    root: Path,
    created: List[str],
    *,
    line: str,
    comment: str,
    also_match: Optional[tuple] = None,
) -> None:
    """Append ``line`` to ``.gitignore`` if missing (idempotent)."""
    gi = root / ".gitignore"
    aliases = {line.strip()}
    if also_match:
        aliases.update(a.strip() for a in also_match)
    if gi.is_file():
        text = gi.read_text(encoding="utf-8")
        existing = {ln.strip() for ln in text.splitlines() if ln.strip()}
        if existing & aliases:
            return
        suffix = "" if text.endswith("\n") or not text else "\n"
        gi.write_text(f"{text}{suffix}\n# {comment}\n{line}\n", encoding="utf-8")
        if ".gitignore" not in created:
            created.append(".gitignore")
        return
    gi.write_text(f"# {comment}\n{line}\n", encoding="utf-8")
    created.append(".gitignore")


def _ensure_temp_gitignore(root: Path, created: List[str]) -> None:
    """Ensure ``/temp/`` is listed in the project's ``.gitignore`` (idempotent)."""
    _ensure_gitignore_line(
        root,
        created,
        line=_TEMP_GITIGNORE_LINE,
        comment="Cuttle agent scratch",
        also_match=("temp/", "/temp/"),
    )


def _ensure_personal_gitignore(root: Path, created: List[str]) -> None:
    """Ensure ``.cuttle/personal/`` is gitignored (idempotent)."""
    _ensure_gitignore_line(
        root,
        created,
        line=_PERSONAL_GITIGNORE_LINE,
        comment="Cuttle install-local overlay",
        also_match=(".cuttle/personal/", ".cuttle/personal/**"),
    )

def ensure_cuttle_scaffold(
    project_root: Union[str, Path],
    *,
    project_name: Optional[str] = None,
) -> List[str]:
    """Create the standard ``.cuttle/`` tree if missing.

    Never overwrites existing files. Returns a list of paths that were created
    (relative to ``project_root``), empty if everything already existed.
    """
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project root does not exist: {root}")

    cuttle = root / ".cuttle"
    created: List[str] = []
    display = (project_name or root.name).strip() or root.name

    def _mkdir(path: Path) -> None:
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path.relative_to(root)).replace("\\", "/"))

    def _write_if_missing(path: Path, text: str) -> None:
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        created.append(str(path.relative_to(root)).replace("\\", "/"))

    _mkdir(cuttle)
    for name in CUTTLE_SUBDIRS:
        _mkdir(cuttle / name)

    _write_if_missing(cuttle / "README.md", _README)
    _write_if_missing(cuttle / "commands" / "README.md", _COMMANDS_README)
    _write_if_missing(cuttle / "GLOBAL.ini", _GLOBAL_INI)
    _write_if_missing(
        cuttle / "rules" / "00-core.md",
        _RULES_CORE.format(name=display),
    )
    # Keep empty dirs visible in git-friendly trees
    for name in ("actions", "memory", "scripts", "docs", "agents", "skills"):
        keep = cuttle / name / ".gitkeep"
        if name == "agents":
            # agents/ holds subdirs later; .gitkeep is enough
            pass
        _write_if_missing(keep, "")

    # Install-local overlay (gitignored on global; projects get a README + subdirs)
    personal = cuttle / "personal"
    _mkdir(personal)
    for sub in ("commands", "rules", "actions", "docs", "scripts", "skills"):
        _mkdir(personal / sub)
    _write_if_missing(personal / "README.md", _PERSONAL_README)
    _ensure_personal_gitignore(root, created)

    # Project-root scratch (global `00-core.md` scratch rule) — dir + gitignore, never overwrite ignore body
    _mkdir(root / PROJECT_TEMP_DIR)
    _ensure_temp_gitignore(root, created)

    return created


def scaffold_many(roots: Iterable[Union[str, Path]]) -> dict:
    """Scaffold several roots; returns ``{path: [created…]}``."""
    out = {}
    for root in roots:
        out[str(Path(root).resolve())] = ensure_cuttle_scaffold(root)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: ``python -m managers.cuttle_scaffold <project_root> [name]``."""
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Scaffold {project}/.cuttle/ if missing")
    p.add_argument("project_root", help="Filesystem path to the project root")
    p.add_argument("--name", default="", help="Display name for rules/00-core.md stub")
    args = p.parse_args(argv)
    try:
        created = ensure_cuttle_scaffold(args.project_root, project_name=args.name or None)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if created:
        print(f"created {len(created)} path(s) under {Path(args.project_root).resolve()}")
        for rel in created:
            print(f"  + {rel}")
    else:
        print(f".cuttle/ already present at {Path(args.project_root).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
