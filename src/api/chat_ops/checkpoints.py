"""Event checkpoints: supervision wakes on what happened, not on the clock.

A schedule's ``checkpoints`` payload maps a checkpoint id to its settings and
its response list (``do``, same entries as ``on_violation``: wake / stop /
action:<name> / supervisor:<profile>):

    {"milestone": {"do": ["wake"]},
     "tests": {"when": "pass", "do": ["supervisor:reviewer"]},
     "repeat_failures": {"n": 3, "do": ["stop", "wake"]},
     "watched_paths": {"regex": "^(schema|api)/", "do": ["stop", "wake"]}}

Detection is deterministic and host-side (session events: ``tool`` with its
command + failed flag, ``tasks`` with items just checked off); the response
runs through ``worker`` like a guard violation. ``stop`` halts the worker
until someone sends it on: that is the "approve before proceeding" gate.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

CHECKPOINTS = {
    'milestone': {
        'label': 'Milestone reached',
        'help': 'A followed Tasks item is checked off or the whole list completes.',
        'ask': 'Check the finished work against its acceptance criteria before it moves on; '
               'if it falls short, send it back with what is missing.',
    },
    'tests': {
        'label': 'Test run finished',
        'help': 'A test command (pytest, npm test, go test, cargo test, …) finishes.',
        'ask': 'Inspect what the run actually covered (which tests, any skips or deselects, '
               'whether they exercise the change) before accepting a pass.',
    },
    'repeat_failures': {
        'label': 'Repeated failures',
        'help': 'The same command fails N times in a row.',
        'ask': 'It is retrying the same failing step. Halt the loop: find the cause, or have it '
               'escalate per its playbook instead of another attempt.',
    },
    'watched_paths': {
        'label': 'Watched files changed',
        'help': 'Files matching a pattern (schemas, API contracts, migrations, …) change.',
        'ask': 'This touches a contract. Review the change; it needs explicit approval before the work builds on it.',
    },
}

# A test runner at a command position (after cd/&&/;/|, env vars, timeout,
# uv/poetry/pipenv run, an optional path prefix), never inside an argument.
TEST_COMMAND = re.compile(
    r'^(?:\S*/)?(?:pytest|py\.test|python3?(?:\.\d+)?\s+-m\s+(?:pytest|unittest)'
    r'|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?::\S+)?|npx\s+(?:jest|vitest|playwright\s+test)|jest|vitest'
    r'|go\s+test|cargo\s+(?:test|nextest)|mvn\w*\s+(?:\S+\s+)*(?:test|verify)'
    r'|gradlew?\s+(?:\S+\s+)*\S*test|dotnet\s+test|ctest|phpunit|rspec|mix\s+test)(?:\s|$)')
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")
_WRAPPERS = re.compile(r'^(?:(?:\w+=\S*|timeout\s+\S+|nice|env|time|uv\s+run|poetry\s+run|pipenv\s+run'
                       r'|xvfb-run|sudo)\s+)+')
DEFAULT_REPEAT = 3
MAX_PATHS = 30


def parse_checkpoints(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Validated checkpoints config ({} when none). Raises ValueError on a bad entry."""
    from api.chat_ops.worker import parse_on_violation

    if raw in (None, '', {}):
        return {}
    if not isinstance(raw, dict):
        raise ValueError('checkpoints must be an object')
    out: Dict[str, Dict[str, Any]] = {}
    for cid, cfg in raw.items():
        if cid not in CHECKPOINTS:
            raise ValueError(f'unknown checkpoint {cid!r}: use {", ".join(CHECKPOINTS)}')
        cfg = cfg if isinstance(cfg, dict) else {}
        if cfg.get('on') is False:
            continue
        entry: Dict[str, Any] = {'do': parse_on_violation(cfg.get('do'))}
        if cid == 'tests':
            when = str(cfg.get('when') or 'pass')
            if when not in ('pass', 'fail', 'any'):
                raise ValueError('tests.when must be pass, fail or any')
            entry['when'] = when
        elif cid == 'repeat_failures':
            n = int(cfg.get('n') or DEFAULT_REPEAT)
            if not 2 <= n <= 20:
                raise ValueError('repeat_failures.n must be 2–20')
            entry['n'] = n
        elif cid == 'watched_paths':
            regex = str(cfg.get('regex') or '').strip()
            if not regex:
                raise ValueError('watched_paths needs a regex')
            try:
                re.compile(regex)
            except re.error as exc:
                raise ValueError(f'watched_paths.regex is not a valid regex: {exc}') from None
            entry['regex'] = regex
        out[cid] = entry
    return out


_SHELL_WRAP = re.compile(r"""^\s*(?:\S*/)?(?:ba|z|da)?sh\s+-\w*c\w*\s+(['"])(.*)\1\s*$""", re.S)


def is_test_command(command: str) -> bool:
    text = str(command or '')
    wrapped = _SHELL_WRAP.match(text)
    if wrapped:  # Codex runs everything as `/bin/bash -lc '<command>'`
        text = wrapped.group(2)
    text = _QUOTED.sub("''", text)
    for part in re.split(r'&&|\|\||[;|\n]', text):
        part = _WRAPPERS.sub('', part.strip())
        if TEST_COMMAND.search(part):
            return True
    return False


def command_signature(command: str) -> str:
    """Same step, retried: whitespace-normalized, volatile numbers dropped."""
    text = re.sub(r'\s+', ' ', str(command or '')).strip()
    text = re.sub(r'\b\d{4,}\b', '#', text)
    return text[:300]


def tool_hits(cfg: Dict[str, Dict[str, Any]], command: str, failed: bool,
              counts: Dict[str, int]) -> Tuple[List[Tuple[str, List[str]]], Dict[str, int]]:
    """Checkpoints a finished tool call fires, and the updated failure counts.

    ``counts`` ({signature: consecutive failures}) lives in the schedule result."""
    hits: List[Tuple[str, List[str]]] = []
    counts = dict(counts or {})
    short = (command or '').strip().splitlines()[0][:200] if command else ''
    if 'tests' in cfg and is_test_command(command):
        when = cfg['tests'].get('when', 'pass')
        if when == 'any' or (when == 'fail') == bool(failed):
            hits.append(('tests', [f"Test run {'failed' if failed else 'passed'}: `{short}`"]))
    if 'repeat_failures' in cfg and command:
        sig = command_signature(command)
        if failed:
            counts[sig] = counts.get(sig, 0) + 1
            if counts[sig] >= cfg['repeat_failures']['n']:
                hits.append(('repeat_failures', [f"`{short}` failed {counts[sig]} times in a row"]))
                counts[sig] = 0
        else:
            counts.pop(sig, None)
        if len(counts) > 50:
            counts = dict(list(counts.items())[-50:])
    return hits, counts


def milestone_lines(event: Dict[str, Any]) -> List[str]:
    title = event.get('title') or event.get('gizmo_id') or 'Tasks'
    lines = [f'Checked off in **{title}**: {text}' for text in (event.get('done') or [])[:10]]
    if event.get('completed'):
        lines.append(f'**{title}** is complete: every item is checked off.')
    return lines


def changed_paths(path: str, base: str) -> Tuple[str, List[str]]:
    """(HEAD, files changed since ``base`` plus uncommitted) in a checkout."""
    def git(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(['git', '-C', path, *args], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    if not path:
        return '', []
    head = (git('rev-parse', 'HEAD') or '').strip()
    files: List[str] = []
    if base and head and base != head:
        files += (git('diff', '--name-only', f'{base}..{head}') or '').split()
    for line in (git('status', '--porcelain', '--untracked-files=all') or '').splitlines():
        name = line[3:].strip()
        if ' -> ' in name:
            name = name.split(' -> ', 1)[1]
        if name:
            files.append(name.strip('"'))
    return head, list(dict.fromkeys(files))


def watched_hits(cfg: Dict[str, Any], path: str, base: str, reported: List[str]) -> Tuple[List[str], str, List[str]]:
    """New files matching the watch regex: (lines, head, all matched so far)."""
    head, files = changed_paths(path, base)
    rx = re.compile(cfg['regex'])
    matched = [f for f in files if rx.search(f)]
    new = [f for f in matched if f not in (reported or [])]
    lines = [f'`{f}`' for f in new[:MAX_PATHS]]
    if len(new) > MAX_PATHS:
        lines.append(f'… and {len(new) - MAX_PATHS} more')
    return lines, head, list(dict.fromkeys(list(reported or []) + matched))[-500:]


def checkin_section(cid: str, lines: List[str]) -> List[str]:
    meta = CHECKPOINTS[cid]
    return ['', f"**Checkpoint — {meta['label']}:**"] + [f'- {ln}' for ln in lines] + ['', meta['ask']]
