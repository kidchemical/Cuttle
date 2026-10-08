"""Reconcile a run's reported edits with its turn snapshot (pure; no I/O).

The turn snapshot (``source=snapshot``, block ``turn-snapshot``) is the ground
truth for what changed. Native edits (``source=native``) are what the CLI said
it changed, and step snapshots (``step-*``) are what Cuttle saw after an
edit-class tool. Per file:

``reported``      changed, and the CLI reported it
``observed``      changed, seen at a tool step, but the CLI did not report it
``unreported``    changed with no per-step evidence (shell writes, formatters…)
``reverted``      reported or observed, but no net change at turn end
``unverifiable``  the snapshot skipped it (binary/oversize/vanished)
``outside``       a reported path outside the snapshot's repository

Line counts are compared only when a single native edit touched a file
(``lines``: ``match`` / ``differs``); several edits to one file cannot be summed
into a net diff, so those stay ``unknown``. Overlapping runs make the turn
``ambiguous`` and nothing is claimed. This never guesses attribution.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

_DIFF_HEADER = re.compile(r'^diff --git a/(.*) b/(.*)$')
# Harnesses whose adapters record native per-step edits. For these, a changed
# file with no native or step evidence is a real "unreported change".
NATIVE_CAPABLE = frozenset({'codex', 'claude', 'opencode', 'cursor', 'muse'})


def change_kind(change: Any) -> str:
    """``add`` / ``delete`` / ``modify`` from our string or Codex's ``{type: …}``."""
    raw = change.get('type') if isinstance(change, dict) else change
    raw = str(raw or '').lower()
    if raw in ('add', 'added', 'create', 'created', 'new'):
        return 'add'
    if raw in ('delete', 'deleted', 'remove', 'removed'):
        return 'delete'
    return 'modify'


def patch_counts(patch: Any, change: Any = None) -> Tuple[Optional[int], Optional[int]]:
    """(additions, deletions) of a unified diff or structured hunks; None if unknown.

    Codex reports an added (or deleted) file as its whole body with no hunk
    header; that body counts as all-added (or all-deleted) lines.
    """
    lines: Iterable[str]
    kind = change_kind(change)
    if isinstance(patch, str) and kind in ('add', 'delete') and '@@' not in patch:
        count = len(patch.splitlines())
        return (count, 0) if kind == 'add' else (0, count)
    if isinstance(patch, str):
        lines = patch.splitlines()
    elif isinstance(patch, list) and all(isinstance(h, dict) for h in patch):
        lines = [line for hunk in patch for line in (hunk.get('lines') or []) if isinstance(line, str)]
    else:
        return None, None
    additions = deletions = 0
    for line in lines:
        if line.startswith(('+++', '---')):
            continue
        if line.startswith('+'):
            additions += 1
        elif line.startswith('-'):
            deletions += 1
    return additions, deletions


def split_patch(text: Any) -> Dict[str, str]:
    """Split a ``git diff`` into per-file patches keyed by the new path."""
    out: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in (text or '').splitlines():
        match = _DIFF_HEADER.match(line)
        if match:
            current = match.group(2)
            out[current] = []
        if current is not None:
            out[current].append(line)
    return {path: '\n'.join(lines) for path, lines in out.items()}


def relative_path(path: Any, repo_root: Optional[str]) -> Optional[str]:
    """Repo-relative POSIX path, or None when it lies outside ``repo_root``."""
    if not isinstance(path, str) or not path.strip():
        return None
    raw = path.strip().replace('\\', '/')
    if not repo_root:
        if os.path.isabs(raw):
            return None
        while raw.startswith('./'):
            raw = raw[2:]
        return raw
    root = os.path.normpath(repo_root).replace('\\', '/')
    if os.path.isabs(raw) or re.match(r'^[A-Za-z]:/', raw):
        full = os.path.normpath(raw).replace('\\', '/')
        if full == root or not full.startswith(root.rstrip('/') + '/'):
            return None
        return full[len(root.rstrip('/')) + 1:]
    rel = os.path.normpath(raw).replace('\\', '/')
    return None if rel.startswith('../') or rel == '..' else rel


def reconcile(edits: List[Dict[str, Any]], agent_id: Optional[str] = None) -> Dict[str, Any]:
    """Run-wide Changes model from a run's edit events (payloads merged)."""
    snapshot = next((e for e in edits if e.get('block_id') == 'turn-snapshot'), None)
    natives = [e for e in edits if e.get('source') == 'native']
    steps = [e for e in edits if e.get('source') == 'snapshot' and str(e.get('block_id', '')).startswith('step-')]
    root = (snapshot or {}).get('repo_root') or next((e.get('repo_root') for e in steps if e.get('repo_root')), None)
    skipped = dict((snapshot or {}).get('skipped') or {})
    files: Dict[str, Dict[str, Any]] = {}

    def entry(rel: str) -> Dict[str, Any]:
        return files.setdefault(rel, {'path': rel, 'additions': None, 'deletions': None,
                                      'in_snapshot': False, 'native': [], 'steps': []})

    for item in (snapshot or {}).get('files') or []:
        if isinstance(item, dict) and item.get('path'):
            row = entry(item['path'])
            row.update(additions=item.get('additions'), deletions=item.get('deletions'), in_snapshot=True)

    outside: List[Dict[str, Any]] = []
    for edit in natives:
        additions, deletions = patch_counts(edit.get('patch'), edit.get('change'))
        ref = {'event_id': edit.get('id'), 'seq': edit.get('seq'), 'tool_id': edit.get('tool_id'),
               'change': change_kind(edit.get('change')), 'additions': additions, 'deletions': deletions}
        rel = relative_path(edit.get('path'), root)
        if rel is None:
            outside.append({**ref, 'path': edit.get('path')})
        else:
            entry(rel)['native'].append(ref)
    for step in steps:
        for rel in split_patch(step.get('text')):
            entry(rel)['steps'].append({'event_id': step.get('id'), 'seq': step.get('seq'),
                                        'tool_id': step.get('tool_id')})

    counts: Dict[str, int] = {}
    for rel, row in files.items():
        evidence = bool(row['native'] or row['steps'])
        if rel in skipped:
            status = 'unverifiable'
        elif not row['in_snapshot']:
            status = 'reverted'
        elif row['native']:
            status = 'reported'
        elif row['steps']:
            status = 'observed'
        else:
            status = 'unreported'
        lines = 'unknown'
        if status == 'reported' and len(row['native']) == 1:
            native = row['native'][0]
            if None not in (native['additions'], native['deletions'], row['additions'], row['deletions']):
                lines = 'match' if (native['additions'], native['deletions']) == (row['additions'], row['deletions']) else 'differs'
        row.update(status=status, lines=lines, evidence=evidence)
        counts[status] = counts.get(status, 0) + 1

    overlap = list((snapshot or {}).get('overlap') or [])
    native_capable = (agent_id or '') in NATIVE_CAPABLE
    if snapshot is None:
        verdict = 'no_snapshot'
    elif not files:
        verdict = 'no_changes'
    elif overlap or (snapshot or {}).get('ambiguous'):
        verdict = 'ambiguous'
    elif not natives and not steps and not native_capable:
        verdict = 'snapshot_only'
    elif counts.get('unreported') or any(r['lines'] == 'differs' for r in files.values()):
        verdict = 'unreported'
    else:
        verdict = 'consistent'
    return {
        'verdict': verdict,
        'repo_root': root,
        'counts': counts,
        'files': sorted(files.values(), key=lambda r: r['path']),
        'outside': outside,
        'overlap': overlap,
        'skipped': skipped,
        'snapshot_event_id': (snapshot or {}).get('id'),
        'start_sha': (snapshot or {}).get('start_sha'),
        'end_sha': (snapshot or {}).get('end_sha'),
        'native_capable': native_capable,
        'native_count': len(natives),
        'step_count': len(steps),
    }
