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

# path -> ceiling (lines). Phase 7 (2026-10-11) lowered each ceiling to the
# post-monolith-refactor baseline measured on branch refactor/work. The three
# composition roots now only compose owned modules (ARCHITECTURE_PRINCIPLES §2);
# new domain logic belongs in an owner, never here.
#
# Pre-refactor baseline 2026-10-08 (release-readiness extractions):
#   chat_page.js 23,250 / app_shell.js 8,159 / web_chat_api.py 7,722.
# Phase 0 measured snapshot (62aca76): 23,052 / 8,089 / 7,741.
# Post-refactor (Phase 7): 13,405 / 4,182 / 5,106.
CEILINGS = {
    # Phase 7 (2026-10-11): 23,420 -> 13,405. chat_page.js is now the page
    # orchestrator: media/lightbox (chat_media), TTS (chat_tts), transcript nav
    # (chat_transcript_nav), launchers (chat_subagent_launchers), agent controls
    # (chat_agent_controls), slash palette (chat_slash_palette), context gauge
    # (chat_context_gauge), session lifecycle (chat_session), history panel
    # (chat_history_panel), stream transport (chat_stream_transport) and the
    # turn/recovery owners hold the moved domains; the page keeps call sites.
    "src/web/js/chat/chat_page.js": 13405,
    # Phase 7 (2026-10-11): 8,280 -> 4,182. app_shell.js now composes the pane
    # lifecycle (shell_panes), Spaces UI (shell_spaces), rail/apps (shell_rail)
    # and Electron chrome/mobile/SSH (shell_electron, shell_mobile) owners.
    "src/web/js/shell/app_shell.js": 4182,
    # Phase 7 (2026-10-11): 7,741 -> 5,106. web_chat_api.py is a composition
    # root + transport boundary: route families moved to api.flask_restart,
    # api.shared_media, api.mobile_companion, agent_router.supervised,
    # agent_harness, agents/<id>/routes.py and settings/providers owners;
    # chat_endpoint lanes moved to api.chat_turn_workflow / api.chat_coordinator.
    "src/api/web_chat_api.py": 5106,
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
