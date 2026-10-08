"""Composition roots must not regrow with new domain implementations.

``chat_page.js``, ``app_shell.js`` and ``web_chat_api.py`` compose owned
modules (ARCHITECTURE_PRINCIPLES.md §2). New chat-domain logic belongs in a
focused ``src/web/js/chat/chat_*.js`` owner, shell logic in its shell/Spaces
owner, and backend behavior in an owned ``api.*`` module.

Two guards:

1. A line ceiling per file. Lower it when a file shrinks; raise it only in the
   same commit as a written justification next to the number.
2. On pull requests (CI sets ``CUTTLE_GROWTH_BASE`` to the target branch), a
   net growth limit per file against the merge base. A change that really
   belongs in the composition root may exceed it when a commit message in the
   range carries a ``Composition-Root-Growth: <file>: <reason>`` trailer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# path -> ceiling (lines). Baseline 2026-10-08 after the release-readiness
# extractions: chat_page.js 23250, app_shell.js 8159, web_chat_api.py 7722.
CEILINGS = {
    "src/web/js/chat/chat_page.js": 23300,
    "src/web/js/shell/app_shell.js": 8200,
    "src/api/web_chat_api.py": 7750,
}
NET_GROWTH_LIMIT = 150
TRAILER = "Composition-Root-Growth:"

OWNER_HINT = (
    "Put new behavior in its owner instead (chat: src/web/js/chat/chat_*.js; "
    "shell: src/web/js/shell or src/web/js/spaces; backend: an api.* module). "
    "See docs/architecture/repository-map.md."
)


def _lines(rel: str) -> int:
    with (REPO_ROOT / rel).open("rb") as fh:
        return sum(1 for _ in fh)


@pytest.mark.parametrize("rel", sorted(CEILINGS))
def test_composition_root_stays_under_ceiling(rel):
    count = _lines(rel)
    assert count <= CEILINGS[rel], (
        f"{rel} has {count} lines (ceiling {CEILINGS[rel]}). {OWNER_HINT} "
        "Raise the ceiling only with a justification in the same commit."
    )


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
        encoding="utf-8", check=True,
    ).stdout


def test_pull_request_net_growth_is_justified():
    base = os.environ.get("CUTTLE_GROWTH_BASE", "").strip()
    if not base:
        pytest.skip("CUTTLE_GROWTH_BASE not set (CI sets it on pull requests)")
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    merge_base = _git("merge-base", base, "HEAD").strip()
    messages = _git("log", "--format=%B", f"{merge_base}..HEAD")
    justified = {
        line.split(TRAILER, 1)[1].split(":", 1)[0].strip()
        for line in messages.splitlines()
        if line.strip().startswith(TRAILER)
    }
    offenders = []
    for rel in sorted(CEILINGS):
        stat = _git("diff", "--numstat", merge_base, "HEAD", "--", rel).split()
        if not stat:
            continue
        net = int(stat[0]) - int(stat[1])
        if net > NET_GROWTH_LIMIT and Path(rel).name not in justified and rel not in justified:
            offenders.append(f"{rel}: +{net} net lines")
    assert not offenders, (
        f"Composition roots grew by more than {NET_GROWTH_LIMIT} lines: {offenders}. {OWNER_HINT} "
        f"If it truly belongs there, add a commit trailer '{TRAILER} <file>: <reason>'."
    )
